"""
This is an example of a Tandem Tales agent and agent factory in Python. The
agent chooses actions using a simple recency-based salience model. The factory
continuously creates new agents as they are needed by the server.

Every decision this agent makes is based on structured protocol data: entity
IDs from action signatures, the `consenting` list on an action, turn types, and
state codes. Natural language `description` fields are never read, because they
are written for language models and are not a reliable basis for a symbolic
policy.
"""
import random
import time
import tt

# Role names as given in the Start message.
PLAYER = 'PLAYER'
GAME_MASTER = 'GAME_MASTER'

# Turn types as given in a Turn object.
SUCCEED = 'SUCCEED'
FAIL = 'FAIL'
PROPOSE = 'PROPOSE'
PASS = 'PASS'


class SalienceAgent(tt.Client):
    """
        A Tandem Tales agent that prefers actions involving recently seen entities.

        Each entity starts at zero salience. Entities appearing in the signature
        of a newly observed turn are refreshed to 1.0, while entities not seen
        have their salience multiplied by 0.5 each turn. The salience of an
        action is the average salience of the entities in its signature.

    Arguments:
        url (str): The URL of the Tandem Tales server.
        port (int): The network port of the Tandem Tales server.
    """

    # Every entity's salience is multiplied by this on each of this agent's turns.
    DECAY = 0.5
    # Salience given to an entity observed in a new turn.
    REFRESH = 1.0
    # As the player, refuse a proposed action scoring at or below this value.
    # Set to None to consent to every proposal.
    REFUSE_AT_OR_BELOW = 0.0
    # The game master gives up control after at most this many turns.
    MAX_TURNS_BEFORE_PASS = 4
    # For this many history turns after the player makes an exchange, the game
    # master will not propose anything that would reverse it.
    UNDO_COOLDOWN = 6
    # Number of most recent world states the agent avoids returning to.
    RECENT_STATES = 6

    # ID number that will be assigned to the next agent created.
    next_id = 0

    def __init__(self, url='localhost', port=tt.DEFAULT_PORT):
        # The arguments to `tt.Client` are:
        # 1. name: This agent's name. Hard-code this. Use up to 20 letters,
        #    digits, and underscores.
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
        self.role = None
        # Entity ID to symbol name, used only to make the logs readable.
        self.entity_names = {}
        # Entity ID to salience value.
        self.salience = {}
        # Salience as it stood before the most recent turn. A proposal is
        # judged against this so it cannot make itself look salient.
        self.prior_salience = {}
        # Entity IDs that could be the player character.
        self.player_ids = set()
        self._processed_history = 0
        # Used to recognize a choice that repeats without changing the world.
        self.last_turn_code = None
        self.last_state_code = None
        self.state_code = None
        self.gm_turns_since_pass = 0
        self.next_forced_pass = 1
        # Codes of the most recent world states, oldest first.
        self.recent_states = []
        # (state code, action code) -> state code the action led to, learned
        # by watching the story. The protocol does not say what an action
        # will do, so the agent remembers what each one did.
        self.transitions = {}
        # (action code, entities) of the most recent action that changed
        # the world state.
        self.last_change = None
        self._observed_history = 0

    def __str__(self):
        return f"Salience Agent {self.id}"


    # Reading structured data out of Turn and Action objects
    @staticmethod
    def _entity_ids(values):
        """Return the IDs of the Entity objects in a list of Value objects."""
        ids = set()
        for value in values or []:
            if (
                isinstance(value, dict)
                and value.get('type') == 'Entity'
                and value.get('id') is not None
            ):
                ids.add(value['id'])
        return ids

    @staticmethod
    def _action_of(turn):
        """Return a turn's Action object, or None for a PASS turn."""
        action = turn.get('action')
        return action if isinstance(action, dict) else None

    @classmethod
    def _signature_entities(cls, turn):
        """Entities named as arguments in a turn's action signature."""
        action = cls._action_of(turn)
        if action is None:
            return set()
        signature = action.get('signature') or {}
        return cls._entity_ids(signature.get('arguments'))

    @classmethod
    def _consenting_entities(cls, turn):
        """Characters who take a turn's action on purpose."""
        action = cls._action_of(turn)
        if action is None:
            return set()
        return cls._entity_ids(action.get('consenting'))

    @classmethod
    def _is_reflexive(cls, turn):
        """
        True if the same entity fills more than one argument position, as in
        attack(Guard, Guard). The entity set collapses the duplicate, so such
        an action scores as if it only involved that one character.
        """
        action = cls._action_of(turn)
        if action is None:
            return False
        arguments = (action.get('signature') or {}).get('arguments') or []
        ids = [
            argument['id'] for argument in arguments
            if isinstance(argument, dict)
            and argument.get('type') == 'Entity'
            and argument.get('id') is not None
        ]
        return len(ids) != len(set(ids))

    @staticmethod
    def _action_code(turn):
        """Identify an action independently of its succeed/fail outcome."""
        action = turn.get('action')
        if not isinstance(action, dict):
            return None
        return action.get('code', action.get('id'))


    # Working out which entity is the player
    def _narrow_player(self, candidates):
        """
        Every turn the player takes is an action the player consents to, so
        the player's entity ID appears in the `consenting` list of all of
        them. Intersecting those lists narrows the field down to the player.
        """
        if not candidates:
            return
        if not self.player_ids:
            self.player_ids = set(candidates)
            return
        narrowed = self.player_ids & set(candidates)
        if narrowed:
            self.player_ids = narrowed

    def _learn_player_from_history(self, turns):
        for turn in turns:
            if turn.get('role') == PLAYER:
                self._narrow_player(self._consenting_entities(turn))

    def _learn_player_from_choices(self, choices):
        """Our own choices as the player all require the player's consent."""
        consenting = [
            self._consenting_entities(choice) for choice in choices
            if self._action_of(choice) is not None
        ]
        consenting = [entities for entities in consenting if entities]
        if consenting:
            self._narrow_player(set.intersection(*consenting))

    def _involves_player(self, turn):
        return bool(self._signature_entities(turn) & self.player_ids)

    def _player_consents(self, turn):
        return bool(self._consenting_entities(turn) & self.player_ids)

    def _acts_on_player(self, turn):
        """The player is affected by the action but is not choosing to act."""
        return self._involves_player(turn) and not self._player_consents(turn)


    #salience model
    def _refresh(self, entities):
        for entity in entities:
            self.salience[entity] = self.REFRESH

    def _update_salience(self, status):
        """Decay every entity, then refresh each newly observed turn once."""
        # Every turn ages all entities by halving their salience.
        for entity in self.salience:
            self.salience[entity] *= self.DECAY

        history = status.get('history') or []
        new_turns = history[self._processed_history:]
        self._processed_history = len(history)

        # Refresh everything except the newest turn, then take a snapshot. The
        # snapshot is what the story looked like just before the turn we are
        # responding to, so a proposal cannot boost its own score.
        for turn in new_turns[:-1]:
            self._refresh(self._signature_entities(turn))
        self.prior_salience = dict(self.salience)
        if new_turns:
            self._refresh(self._signature_entities(new_turns[-1]))

        self._learn_player_from_history(new_turns)

    def _score(self, turn, salience=None):
        """Average salience of the entities in the turn's action signature."""
        salience = self.salience if salience is None else salience
        entities = self._signature_entities(turn)
        if not entities:
            return 0.0
        # Averaging keeps actions with different parameter counts comparable.
        return sum(salience.get(entity, 0.0) for entity in entities) / len(entities)

    def _repeats_last(self, turn):
        """True if this choice did nothing the last time it was made."""
        return (
            self.last_turn_code is not None
            and turn.get('code') == self.last_turn_code
            and self.state_code == self.last_state_code
        )

    def _recent_player_exchanges(self, history):
        """
        Entity sets of actions the player agreed to that actually happened in
        the last UNDO_COOLDOWN turns, such as a trade or accepting a gift.
        """
        return [
            self._signature_entities(turn)
            for turn in history[-self.UNDO_COOLDOWN:]
            if turn.get('type') == SUCCEED and self._player_consents(turn)
        ]

    def _undoes_recent_exchange(self, turn, recent):
        """
        True if this is a request to the player that uses only entities from
        an exchange the player just made. Reversing a trade, or asking for
        an item back, involves the same people and items as the original, so
        its entities are a subset of the original's.
        """
        if turn.get('type') != PROPOSE:
            return False
        entities = self._signature_entities(turn)
        return bool(entities) and any(entities <= previous for previous in recent)

    # Avoiding recent states.
    def _record_state(self, status):
        """
        The server only sends the current state, not past ones, so the agent
        keeps its own list of recent states. When the state changes, it also
        remembers which action caused the change, so it knows next time where
        that action leads.
        """
        history = status.get('history') or []
        new_turns = history[self._observed_history:]
        self._observed_history = len(history)

        code = (status.get('state') or {}).get('code')
        previous = self.recent_states[-1] if self.recent_states else None
        # PASS, PROPOSE and FAIL turns leave the world unchanged.
        if code is None or code == previous:
            return

        changes = [turn for turn in new_turns if turn.get('type') == SUCCEED]
        if changes:
            action = changes[-1]
            # With exactly one new turn, the state it started from is certain.
            if len(new_turns) == 1 and previous is not None:
                self.transitions[(previous, self._action_code(action))] = code
            self.last_change = (
                self._action_code(action),
                self._signature_entities(action),
            )
        self.recent_states.append(code)
        self.recent_states = self.recent_states[-self.RECENT_STATES:]

    def _returns_to_recent_state(self, turn):
        """
        Would this choice put the world back in a state it was recently in?
        Returns why ('learned' or 'predicted'), or None if not.

        learned: this action was taken from this exact state before, and it
        led to a state that is still recent.

        predicted: the action has not been seen from here, but it uses exactly
        the same entities as the action that just changed the world, so it is
        that action's reverse. trade(Adventurer, Coin, Bandit, Crown) is
        reversed by trade(Adventurer, Crown, Bandit, Coin), and
        drop(Bandit, Flower) by pickup(Bandit, Flower). Undoing the last
        change returns the world to the state just before it.
        """
        # Only these turn types can change the world.
        if turn.get('type') not in (SUCCEED, PROPOSE):
            return None
        code = self._action_code(turn)

        known = self.transitions.get((self.state_code, code))
        if known is not None and known in self.recent_states:
            return 'learned'

        if self.last_change is not None:
            last_code, last_entities = self.last_change
            entities = self._signature_entities(turn)
            if code != last_code and len(entities) >= 2 and entities == last_entities:
                return 'predicted'
        return None

    def _skip_recent_state(self, turn, allow_player_choice=False):
        """
        Filter helper that also logs why a choice was skipped. The game master
        passes allow_player_choice=True so it never blocks an action the
        player chose to take: a SUCCEED of an action the player consents to
        is the game master answering the player's own proposal.
        """
        if (
            allow_player_choice
            and turn.get('type') == SUCCEED
            and self._player_consents(turn)
        ):
            return False
        reason = self._returns_to_recent_state(turn)
        if reason:
            action = self._action_of(turn) or {}
            print(
                f"{self} skips {turn.get('type')} {action.get('name')}: "
                f"would return to a recent state ({reason})."
            )
        return reason is not None

    def _refused_action_codes(self, history):
        """Actions the player has already refused."""
        return {
            self._action_code(turn)
            for turn in history
            if turn.get('role') == PLAYER and turn.get('type') == FAIL
        }


    #Choosing logic
    def _log_scores(self, choices, scores):
        print(f"{self} salience scores:")
        # Most salient first; equal scores keep the server's original order.
        for index in sorted(scores, key=lambda i: (-scores[i], i)):
            entities = self._signature_entities(choices[index])
            values = ', '.join(
                f"{self.entity_names.get(entity, entity)}="
                f"{self.salience.get(entity, 0.0):.3f}"
                for entity in sorted(entities)
            )
            name = (self._action_of(choices[index]) or {}).get('name', choices[index].get('type'))
            print(f"  [{index}] {scores[index]:.3f}: {choices[index].get('type')} {name} ({values})")

    def _best(self, choices, candidates):
        scores = {index: self._score(choices[index]) for index in candidates}
        self._log_scores(choices, scores)
        highest = max(scores.values())
        # First keep only the most salient actions.
        top = [index for index in candidates if scores[index] == highest]

        def tiebreak(index):
            # Among equally salient actions, prefer, in order:
            # 1. actions involving the player,
            # 2. actions that happen (SUCCEED) over failed attempts, since a
            #    FAIL leaves the world unchanged,
            # 3. actions connecting more of the entities seen so far.
            choice = choices[index]
            salient = sum(
                1 for entity in self._signature_entities(choice)
                if self.salience.get(entity, 0.0) > 0.0
            )
            return (
                self._involves_player(choice),
                choice.get('type') == SUCCEED,
                salient,
            )

        best = max(tiebreak(index) for index in top)
        top = [index for index in top if tiebreak(index) == best]
        # If the tie still remains, make the final choice randomly.
        return random.choice(top)

    def _choose_as_player(self, choices, history):
        succeed = [i for i, c in enumerate(choices) if c.get('type') == SUCCEED]
        refuse = [i for i, c in enumerate(choices) if c.get('type') == FAIL]

        # The game master proposed an action, so the only decision is whether
        # to consent to it.
        if succeed and refuse:
            score = self._score(choices[succeed[0]], self.prior_salience)
            established = max(self.prior_salience.values(), default=0.0) > 0.0
            if (
                self.REFUSE_AT_OR_BELOW is not None
                and established
                and score <= self.REFUSE_AT_OR_BELOW
            ):
                print(f"{self} refuses a proposal scoring {score:.3f}.")
                return refuse[0]
            print(f"{self} consents to a proposal scoring {score:.3f}.")
            return succeed[0]

        # Otherwise propose something. Passing wastes the turn, so it is only
        # used when nothing else is available.
        refused = self._refused_action_codes(history)
        candidates = [
            i for i, c in enumerate(choices)
            if c.get('type') != PASS
            and self._action_code(c) not in refused
            and not self._is_reflexive(c)
            and not self._skip_recent_state(c)
            and not self._repeats_last(c)
        ]
        return self._with_fallbacks(choices, candidates)

    def _choose_as_game_master(self, choices, history):
        pass_index = next(
            (i for i, c in enumerate(choices) if c.get('type') == PASS), None
        )
        player_just_passed = bool(history) and (
            history[-1].get('role') == PLAYER and history[-1].get('type') == PASS
        )

        refused = self._refused_action_codes(history)
        recent = self._recent_player_exchanges(history)
        candidates = [
            i for i, c in enumerate(choices)
            if c.get('type') != PASS
            and self._action_code(c) not in refused
            # Do not do things to the player that the player did not agree to.
            and not self._acts_on_player(c)
            # Do not narrate the player trying and failing.
            and not (c.get('type') == FAIL and self._player_consents(c))
            # Do not ask the player to undo something they just did.
            and not self._undoes_recent_exchange(c, recent)
            # Do not have characters act on themselves, unless the player
            # chose to do that to themselves.
            and not (self._is_reflexive(c) and not self._player_consents(c))
            # Do not put the world back in a state it was recently in.
            and not self._skip_recent_state(c, allow_player_choice=True)
            and not self._repeats_last(c)
        ]

        if pass_index is not None and not player_just_passed:
            self.gm_turns_since_pass += 1
            player_is_focus = bool(candidates) and self._involves_player(
                choices[max(candidates, key=lambda i: self._score(choices[i]))]
            )
            if (
                not candidates
                or self.gm_turns_since_pass >= self.next_forced_pass
                or player_is_focus
            ):
                self._reset_pass_timer()
                print(f"{self} passes control to the player.")
                return pass_index

        if not candidates and pass_index is not None:
            # Everything left would decide the player's fate without their
            # consent. Hand control back instead, even if the player just
            # passed to us.
            self._reset_pass_timer()
            print(f"{self} has nothing safe to do and passes control to the player.")
            return pass_index

        index = self._with_fallbacks(choices, candidates)
        if choices[index].get('type') == PROPOSE:
            #Proposing hands control to the player just as passing does
            self._reset_pass_timer()
        return index

    def _with_fallbacks(self, choices, candidates):
        """
        A filter can remove every candidate, for example when a previously
        refused proposal is the only available action. Preserve a valid
        protocol choice instead of attempting to score an empty list.
        """
        if not candidates:
            candidates = [i for i, c in enumerate(choices) if c.get('type') != PASS]
        if not candidates:
            candidates = list(range(len(choices)))
        return self._best(choices, candidates)

    def _reset_pass_timer(self):
        self.gm_turns_since_pass = 0
        self.next_forced_pass = random.randint(1, self.MAX_TURNS_BEFORE_PASS)


    #Client callbacks
    def on_connect(self, connect):
        """
        Optional: Runs when the client connects to the server.
        """
        print(f"{self} has connected to the server.")

    def on_start(self, world, role, state):
        """
        Optional: Runs when the client starts its session.
        """
        self.role = role
        self.entity_names = {
            entity['id']: entity.get('name', entity['id'])
            for entity in world.get('entities', [])
            if entity.get('id') is not None
        }
        self.salience = {entity: 0.0 for entity in self.entity_names}
        self.prior_salience = dict(self.salience)
        # Seed the player guess from the conventional symbol name, then let
        # the consent intersection confirm or correct it.
        self.player_ids = {
            entity for entity, name in self.entity_names.items()
            if str(name).lower() == 'player'
        }
        self._processed_history = 0
        self.last_turn_code = None
        self.last_state_code = None
        self.state_code = None
        initial = (state or {}).get('code')
        self.recent_states = [initial] if initial is not None else []
        self.transitions = {}
        self.last_change = None
        self._observed_history = 0
        self._reset_pass_timer()
        print(f"{self} has started its session as the {role} in world \"{world['name']}\".")

    def on_update(self, status):
        """
        Optional: Runs each time the client sees a story world update, whether
        or not it is the client's turn.
        """
        self._record_state(status)

    def on_choice(self, status):
        """
        Required: Runs each time the world updates and it is the client's turn.
        """
        choices = status['choices']
        history = status.get('history') or []
        self.state_code = (status.get('state') or {}).get('code')
        self._update_salience(status)

        if self.role == PLAYER:
            self._learn_player_from_choices(choices)
            index = self._choose_as_player(choices, history)
        else:
            index = self._choose_as_game_master(choices, history)

        self.last_turn_code = choices[index].get('code')
        self.last_state_code = self.state_code
        action = self._action_of(choices[index])
        name = action.get('name') if action else 'nothing'
        print(f"{self} chooses: {choices[index].get('type')} {name}")
        # Keep the response prompt while avoiding stale choices after a stop
        time.sleep(0.1)
        return index

    def on_end(self, ending):
        """
        Optional: Runs when the story reaches an ending.
        """
        print(f"{self} has reached an ending: {ending.get('name')}")

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
        if message is None:
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
if __name__ == '__main__':
    factory = SalienceAgentFactory()
    factory.run()