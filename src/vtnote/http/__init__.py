"""HTTP transport boundary for the local FastAPI application.

Modules in this package may depend on application services, but application and
domain modules must not import from ``vtnote.http``.  Keeping request contracts
here prevents the composition root from becoming the owner of every API type.
"""
