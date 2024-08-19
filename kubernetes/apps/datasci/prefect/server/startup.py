import os
from sqlalchemy.engine.url import make_url

# Get the original connection URL
original_url = os.environ.get('PREFECT_API_DATABASE_CONNECTION_URL')

if original_url:
    # Create a SQLAlchemy URL object
    url = make_url(original_url)

    # Remove 'jit' from query parameters if it exists
    if 'jit' in url.query:
        del url.query['jit']

    # Set the modified URL back to the environment variable
    os.environ['PREFECT_API_DATABASE_CONNECTION_URL'] = str(url)

# Continue with the regular Prefect server startup
from prefect.server import start

start()
