"""
This is an example of a Tandem Tales agent and agent factory in Python. The
agent chooses actions using a simple recency-based salience model. The factory
continuously creates new agents as they are needed by the server.
"""
import random
import time
import tt

class SalienceAgent(tt.Client):
    """
        A Tandem Tales agent that prefers actions involving recently seen entities.

        Each action parameter starts at zero. Parameters seen during a turn are
        refreshed to 1.0, while parameters not seen lose 0.5 salience per turn.
        The salience of an action is the average of its parameter saliences.

    Arguments:
        url (str): The URL of the Tandem Tales server.
        port (int): The network port of the Tandem Tales server.
    """

    # ID number that will be assigned to the next agent created.
    next_id = 0

    def __init__(self, url='localhost', port=tt.DEFAULT_PORT):
        # The arguments to `tt.Client` are:
        # 1. name: This agent's name. Hard-code this. Use up to 20 letters,
        #    digits, and undersocres.
        # 2. password: This should be left None so the Client will read it from
        #    the environment variables. Do not hard-code the password.
        # 3. world: The name of the world the agent wants to play in. Leave this
        #    None to play any world. Hard-code this if the agent is only
        #    designed to play in one story world.
        # 4. role: The role this client will have, which is either tt.PLAYER or
        #    tt.GM (for game master), or None for either role. Hard-code this if
        #    the agent is only designed to play as one role.
        # 5. partner: The partner this agents wants to play with. Leave this
        #    None to play with any partner. Hard-code this if the agent is only
        #    designed to play with one type of partner.
        # 6. key: The API key used for the external API. This should be left
        #    None so the Client will read it from the environment variables.
        # 7. url: The URL of the Tandem Tales server.
        # 8. port: The network port of the Tandem Tales server.
        super().__init__('salience', None, None, None, None, None, url, port)
        self.id = SalienceAgent.next_id
        SalienceAgent.next_id += 1
        # Parameters are discovered from the current world's signatures.
        self.salience = {}
        self._known_parameters = set()
        # Keep a score for each complete action so it can be inspected in logs.
        self.action_salience = {}
        # Used to recognize actions that repeat without changing the world.
        self.player_names = set()
        self.last_action_key = None
        self.last_action_state = None
        self.role = None
        self.gm_turns_since_pass = 0
        self.next_forced_pass = 0
        self.stopped = False

    def __str__(self):
        return f"Salience Agent {self.id}"

    @staticmethod
    def _scalar_values(value):
        """Return meaningful scalar values from a nested action field."""
        if isinstance(value, dict):
            values = []
            for key, item in value.items():
                if key not in ('description', 'type', 'name', 'action'):
                    values.extend(SalienceAgent._scalar_values(item))
            return values
        if isinstance(value, (list, tuple)):
            values = []
            for item in value:
                values.extend(SalienceAgent._scalar_values(item))
            return values
        if isinstance(value, (str, int, float)) and not isinstance(value, bool):
            return [str(value)]
        return []

    @classmethod
    def _action_entities(cls, choice):
        """Extract parameters represented by an available action signature."""
        action = choice.get('action', choice)
        return cls._signature_parameters(action.get('signature', {}))

    @classmethod
    def _signature_parameters(cls, signature):
        """Return entity names used as parameters in a signature object."""
        if not isinstance(signature, dict):
            return set()
        parameters = set()
        for argument in signature.get('arguments', []):
            if isinstance(argument, dict):
                if argument.get('type') == 'Entity' and argument.get('name'):
                    parameters.add(cls._normalize_parameter(argument['name']))
                parameters.update(cls._signature_parameters(argument))
        return parameters

    @staticmethod
    def _normalize_parameter(value):
        return str(value).lower().replace('_', ' ')

    def _parameter_entities(self, value):
        """Map story text to parameters discovered from signatures."""
        if not isinstance(value, str):
            return set()
        text = value.lower().replace('_', ' ')
        entities = set()
        for parameter in self._known_parameters:
            if parameter in text:
                entities.add(parameter)
        return entities

    @staticmethod
    def _is_player_value(value):
        value = value.lower()
        return value in ('player', 'the player') or 'player' in value

    def _observed_values(self, value):
        """Extract identifiers and values from the visible-entity mapping."""
        if isinstance(value, str):
            return list(self._parameter_entities(value))
        if isinstance(value, dict):
            values = []
            for key, item in value.items():
                values.extend(self._parameter_entities(key))
                values.extend(self._observed_values(item))
            return values
        if isinstance(value, (list, tuple)):
            values = []
            for item in value:
                values.extend(self._observed_values(item))
            return values
        return self._scalar_values(value)

    def _update_salience(self, choices, status):
        """Decay unseen entities and refresh entities visible this turn."""
        # Every turn ages all parameters, but never below zero.
        for entity in self.salience:
            self.salience[entity] = max(0.0, self.salience[entity] - 0.5)

        # Only the current world, visible descriptions, and the latest event
        # refresh salience. Available future choices do not.
        visible = set(self._observed_values(status.get('state', {})))
        visible.update(self._observed_values(status.get('descriptions', {})))
        history = status.get('history', [])
        if history:
            visible.update(self._observed_values(history[-1]))

        for entity in visible:
            self.salience.setdefault(entity, 0.0)
            self.salience[entity] = 1.0

        self.player_names.update(
            entity for entity in visible if self._is_player_value(entity)
        )

    def _action_score(self, choice):
        # Averaging keeps actions with different parameter counts comparable.
        entities = self._action_entities(choice)
        if not entities:
            return 0.0
        return sum(self.salience.get(entity, 0.0) for entity in entities) / len(entities)

    @staticmethod
    def _action_key(choice):
        return (
            choice.get('type'),
            choice.get('description'),
            repr(choice.get('parameters', choice.get('arguments', choice.get('args')))),
        )

    @classmethod
    def _action_identity(cls, choice):
        """Identify an action independently of succeed/fail outcome."""
        action = choice.get('action', choice)
        return (
            action.get('id'),
            action.get('code'),
            action.get('name'),
            repr(action.get('signature')),
        )

    @classmethod
    def _is_pass(cls, choice):
        return choice.get('type') == 'PASS'

    def _rejected_actions(self, history):
        """Return actions previously rejected by the player."""
        return {
            self._action_identity(turn)
            for turn in history
            if turn.get('role') == 'PLAYER' and turn.get('type') == 'FAIL'
        }

    @staticmethod
    def _player_just_passed(history):
        return bool(history) and (
            history[-1].get('role') == 'PLAYER'
            and history[-1].get('type') == 'PASS'
        )

    def _pass_choice_index(self, choices):
        return next(
            (index for index, choice in enumerate(choices) if self._is_pass(choice)),
            None,
        )

    def _player_action_choices(self, choices):
        return [
            choice for choice in choices
            if not self._is_pass(choice) and self._involves_player(choice)
        ]

    def _involves_player(self, choice):
        entities = self._action_entities(choice)
        description = choice.get('description', '')
        return (
            bool(entities & self.player_names)
            or any(self._is_player_value(entity) for entity in entities)
            or 'player' in description.lower()
            or 'you' in description.lower()
        )

    def on_connect(self, connect):
        """
        Optional: Runs when the client connects to the server.
        """
        print(f"{self} has connected to the server.")
    
    def on_start(self, world, role, state):
        """
        Optional: Runs when the client starts its session.
        """
        self._known_parameters = set()
        for action in world.get('actions', []):
            self._known_parameters.update(
                self._signature_parameters(action.get('signature', {}))
            )
        self.salience = {
            parameter: self.salience.get(parameter, 0.0)
            for parameter in self._known_parameters
        }
        self.role = role
        self.gm_turns_since_pass = 0
        self.next_forced_pass = random.randint(0, 3)
        self.stopped = False
        print(f"{self} has started its session as the {role} in world \"{world['name']}\".")
    
    def on_update(self, status):
        """
        Optional: Runs each time the client sees a story world update, whether
        or not it is the client's turn.
        """
        pass
    
    def on_choice(self, status):
        """
        Required: Runs each time the world updates and it is the client's turn.
        """
        original_choices = status['choices']
        self._update_salience(original_choices, status)
        rejected_actions = self._rejected_actions(status.get('history', []))
        choices_with_indices = [
            (index, choice)
            for index, choice in enumerate(original_choices)
            if self._action_identity(choice) not in rejected_actions
        ]

        if self.role == 'PLAYER':
            choices_with_indices = [
                (index, choice) for index, choice in choices_with_indices
                if choice.get('type') != 'FAIL'
            ]
        elif self.role == 'GAME_MASTER':
            choices_with_indices = [
                (index, choice) for index, choice in choices_with_indices
                if not (
                    choice.get('type') == 'FAIL'
                    and self._involves_player(choice)
                )
                ]

        choice_indices = [index for index, choice in choices_with_indices]
        choices = [choice for index, choice in choices_with_indices]
        pass_index = self._pass_choice_index(choices)
        player_just_passed = self._player_just_passed(status.get('history', []))
        if (
            self.role == 'GAME_MASTER'
            and pass_index is not None
            and not player_just_passed
        ):
            self.gm_turns_since_pass += 1
            scores = [self._action_score(choice) for choice in choices]
            actionable_scores = [
                score for choice, score in zip(choices, scores)
                if not self._is_pass(choice)
            ]
            highest_action_score = max(actionable_scores, default=float('-inf'))
            most_salient_player_action = any(
                not self._is_pass(choice)
                and self._involves_player(choice)
                and score == highest_action_score
                for choice, score in zip(choices, scores)
            )
            if (
                self.gm_turns_since_pass >= self.next_forced_pass
                or most_salient_player_action
            ):
                self.gm_turns_since_pass = 0
                self.next_forced_pass = random.randint(0, 3)
                print(f"{self} passes control to the player.")
                return choice_indices[pass_index]

        state_signature = repr(status.get('state', {}))
        scores = []
        for choice in choices:
            score = self._action_score(choice)
            # Avoid getting stuck repeating an action that did not change state.
            if (
                self.last_action_key == self._action_key(choice)
                and self.last_action_state == state_signature
            ):
                score -= 100.0
            self.action_salience[self._action_key(choice)] = score
            scores.append(score)

        print(f"{self} salience scores:")
        for index, choice in enumerate(choices):
            entities = self._action_entities(choice)
            values = ', '.join(
                f"{entity}={self.salience.get(entity, 0.0):.1f}"
                for entity in sorted(entities)
            )
            print(f"  [{index + 1}] {scores[index]:.1f}: {choice['description']} ({values})")

        highest = max(scores)
        # First keep only the most salient actions.
        candidates = [
            index for index, score in enumerate(scores) if score == highest
        ]

        player_candidates = [
            index for index in candidates if self._involves_player(choices[index])
        ]
        if player_candidates:
            # A player-involving action wins a tie among equally salient ones.
            candidates = player_candidates
        # If the tie still remains, make the final choice randomly.
        choice = random.choice(candidates)
        self.last_action_key = self._action_key(choices[choice])
        self.last_action_state = state_signature

        print(f"{self} chooses: \"{choices[choice]['description']}\"")
        # Keep the response prompt while avoiding stale choices after a stop.
        time.sleep(0.1)
        return choice_indices[choice]
    
    def on_end(self, ending):
        """
        Optional: Runs when the story reaches an ending.
        """
        print(f"{self} has reached an ending: \"{ending['description']}\"")
    
    def on_close(self):
        """
        Optional: Runs when the client stops normally by the `close` method or
        because the story ended. Does not run if client crashes.
        """
        print(f"{self} has closed.")
    
    def on_stop(self, message):
        """
        Optional: Runs when the session stops.
        """
        self.stopped = True
        if message == None:
            print(f"{self} has stopped.")
        else:
            print(f"{self} has stopped: \"{message}\"")
    
    def on_disconnect(self):
        """
        Optional: Run when the client disconnects from the server.
        """
        print(f"{self} has disconnected.")

class SalienceAgentFactory(tt.Factory):
    """
    This factory creates new agents as they are needed by the server. A single
    agent only exists for one story session. When an agent that is waiting for a
    session finds a partner, this factory starts a new agent to take that
    agent's place.
    
    Arguments:
        max (int): The maximum number of clients that may be running at a time
            or 0 for no limit.
    """
    
    def __init__(self, max=0):
        super().__init__(max)
    
    def __str__(self):
        return 'Salience Agent Factory'
    
    def on_start(self):
        """
        Optional: Runs when the factory starts.
        """
        print(f"{self} has started.")
    
    def create(self):
        """
        Required: Creates a new client.
        """
        return SalienceAgent()
    
    def on_close(self):
        """
        Optional: Runs when the factory is closed or because a client raised an
        exception. Does not run if the factory is interrupted.
        """
        print(f"{self} has been closed.")
    
    def on_stop(self):
        """
        Optional: Runs when the factory has stopped running and all clients
        have finished their sessions and disconnected.
        """
        print(f"{self} has stopped.")

# Start a new factory and run until it is closed or interrupted.
factory = SalienceAgentFactory()
factory.run()
