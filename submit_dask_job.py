import asyncio
from dask.distributed import Client, LocalCluster
import dask.array as da
import time

DASK_SCHEDULER_ADDRESS = "tcp://dask.istio.local:8786"

async def submit_job():
    print(f"Attempting to connect to Dask scheduler at: {DASK_SCHEDULER_ADDRESS}")
    try:
        # Connect to the Dask cluster
        # Setting timeout to 30 seconds for connection attempts
        async with Client(DASK_SCHEDULER_ADDRESS, timeout="30s", asynchronous=True) as client:
            print(f"Successfully connected to Dask scheduler: {client.scheduler_info()}")

            # Create a Dask array (example computation)
            print("Creating a Dask array...")
            array = da.random.random((10000, 10000), chunks=(1000, 1000))

            # Perform a computation
            print("Performing a computation (mean)...")
            result = array.mean()

            # Compute the result
            start_time = time.time()
            computed_result = await client.compute(result) # Use await for asynchronous client
            end_time = time.time()

            print(f"Computation finished in {end_time - start_time:.2f} seconds.")
            print(f"Result of the computation: {computed_result}")

            # You can also submit tasks
            def inc(x):
                return x + 1

            def add(x, y):
                return x + y

            print("Submitting simple tasks (inc, add)...")
            x = client.submit(inc, 10)
            y = client.submit(inc, 20)
            total = client.submit(add, x, y)

            computed_total = await client.gather(total) # Use await for asynchronous client
            print(f"Result of inc(10) + inc(20): {computed_total}")

            print("Dask job submission test completed successfully.")

    except asyncio.TimeoutError:
        print(f"Connection to {DASK_SCHEDULER_ADDRESS} timed out. Check if the scheduler is accessible and the address is correct.")
        print("Ensure that the Istio Gateway and TCPRoute changes have been applied by Flux.")
    except OSError as e:
        print(f"OSError connecting to {DASK_SCHEDULER_ADDRESS}: {e}")
        print("This might indicate a problem with network connectivity or the Dask scheduler endpoint.")
        print("Verify that dask.istio.local:8786 resolves correctly and is reachable.")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        print(f"Type of error: {type(e)}")

if __name__ == "__main__":
    # For versions of dask.distributed that use tornado's IOLoop for async operations:
    # asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy()) # if on windows and needed
    asyncio.run(submit_job())
