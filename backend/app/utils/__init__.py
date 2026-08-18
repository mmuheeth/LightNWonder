"""Low-level helpers that are not business logic.

Modules here talk to the operating system or parse foreign file formats. They
hold no application state and raise no :class:`~app.exceptions.base.AppException`
-- services translate their failures into the HTTP contract.
"""
