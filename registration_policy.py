"""Which role a self-registering caller may get.

Registration is open to anyone who can reach the route, so the caller must never choose their own authority. The only roles a request may ask for are the two
that cannot change anything for anyone else; every other role (supervisor, admin, anything unknown) is refused, and a request that names none gets the default.
Raising a role is a separate, authenticated action, not part of registering.

Every route that creates an operator from a request body calls this, so a new route cannot decide for itself.
"""

DEFAULT_REGISTRATION_ROLE = "operator"
PUBLIC_REGISTRATION_ROLES = ("observer", "operator")


class RegistrationRoleError(ValueError):
    """The request asked for a role that registration does not grant."""


def public_registration_role(requested=None):
    """The role name to give a self-registering caller: the default when none is named, the named one when it is public, otherwise an error."""
    if requested is None:
        return DEFAULT_REGISTRATION_ROLE
    if not isinstance(requested, str):
        raise RegistrationRoleError("invalid registration role")
    role = requested.strip().lower()
    if not role:
        return DEFAULT_REGISTRATION_ROLE
    if role not in PUBLIC_REGISTRATION_ROLES:
        raise RegistrationRoleError("invalid registration role")
    return role
