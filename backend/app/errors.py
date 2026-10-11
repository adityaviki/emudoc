"""The base of the errors the app raises on purpose: a message the user can act on, and whether
the cause is expected to pass on its own."""


class AppError(Exception):
    """An error whose message is a complete sentence a user or operator can act on.

    `transient` says the cause is one that passes by itself (a provider that is down or rate
    limiting, a network that dropped the request), so that work which hit it is worth running
    again in a while. It is set where the error is made, from what the provider answered, never
    guessed from the message. Everything else needs a person to change something first.
    """

    def __init__(self, message: str, *, transient: bool = False):
        super().__init__(message)
        self.transient = transient
