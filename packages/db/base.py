from sqlalchemy.orm import DeclarativeBase

# In SQLAlchemy 2.0+, inheriting from DeclarativeBase is the standard way 
# to create a "Registry" for your database models.
class Base(DeclarativeBase):
    """
    This is the foundational class for all database models.
    
    When you create a model (like 'User' or 'Product'), it will inherit from 
    this 'Base'. SQLAlchemy uses this to:
    1. Track all tables defined in your application.
    2. Map Python classes to SQL tables.
    3. Manage 'metadata' (the schema definition) used to create or drop tables.
    """
    pass
