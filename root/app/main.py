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
        The salience of an action is the sum of its parameter saliences.

    Arguments:
        url (str): The URL of the Tandem Tales server.
        port (int): The network port of the Tandem Tales server.
    """

    # ID number that will be assigned to the next agent created.
    next_id = 0

    # These are the only story concepts that can contribute to salience.
    PARAMETERS = (
        'player',
        'gamemaster',
        'barista',
        'coffee',
        'herbal tea',
        'shop',
        'outside',
        'money',
    )

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
        self.salience = {parameter: 0.0 for parameter in self.PARAMETERS}
        # Keep a score for each complete action so it can be inspected in logs.
        self.action_salience = {}
        # Used to recognize actions that repeat without changing the world.
        self.player_names = set()
        self.last_action_key = None
        self.last_action_state = None

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
        """Extract entities and locations represented by an available action."""
        # The action description is the readable form supplied by the server.
        return cls._parameter_entities(choice.get('description', ''))

    @classmethod
    def _parameter_entities(cls, value):
        """Map story text to the fixed set of story parameters."""
        if not isinstance(value, str):
            return set()
        text = value.lower().replace('_', ' ')
        entities = set()
        # Normalize wording such as "you" and "game master" to our keys.
        if 'player' in text or 'you' in text:
            entities.add('player')
        if 'game master' in text or 'gamemaster' in text:
            entities.add('gamemaster')
        for parameter in cls.PARAMETERS[2:]:
            if parameter in text:
                entities.add(parameter)
        return entities

    @staticmethod
    def _is_player_value(value):
        value = value.lower()
        return value in ('player', 'the player') or 'player' in value

    @classmethod
    def _observed_values(cls, value):
        """Extract identifiers and values from the visible-entity mapping."""
        if isinstance(value, str):
            return list(cls._parameter_entities(value))
        if isinstance(value, dict):
            values = []
            for key, item in value.items():
                values.extend(cls._parameter_entities(key))
                values.extend(cls._observed_values(item))
            return values
        if isinstance(value, (list, tuple)):
            values = []
            for item in value:
                values.extend(cls._observed_values(item))
            return values
        return cls._scalar_values(value)

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
        # An action is more salient when it mentions more recently seen items.
        entities = self._action_entities(choice)
        return sum(self.salience.get(entity, 0.0) for entity in entities)

    @staticmethod
    def _action_key(choice):
        return (
            choice.get('type'),
            choice.get('description'),
            repr(choice.get('parameters', choice.get('arguments', choice.get('args')))),
        )

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
        choices = status['choices']
        self._update_salience(choices, status)
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
        # Wait a random number of seconds to create the illusion of thinking.
        time.sleep(random.randint(2, 5))
        return choice
    
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
