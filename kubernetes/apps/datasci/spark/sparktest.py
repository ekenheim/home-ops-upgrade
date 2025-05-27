from pyspark.sql import SparkSession
import logging
import os
import time
import socket

# Set Python version environment variables
os.environ['PYSPARK_PYTHON'] = '/Users/regent/Library/Caches/pypoetry/virtualenvs/ai-hedge-fund-L7eNC-qM-py3.13/bin/python3'
os.environ['PYSPARK_DRIVER_PYTHON'] = '/Users/regent/Library/Caches/pypoetry/virtualenvs/ai-hedge-fund-L7eNC-qM-py3.13/bin/python3'

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logging.getLogger('py4j').setLevel(logging.DEBUG)
logging.getLogger('pyspark').setLevel(logging.DEBUG)

def wait_for_port(host, port, timeout=60):
    """Wait for a port to be available."""
    start_time = time.time()
    while True:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except (socket.timeout, socket.error):
            if time.time() - start_time > timeout:
                return False
            time.sleep(1)

# Wait for Spark master to be available
print("Waiting for Spark master to be available...")
if not wait_for_port("spark.istio.local", 7077):
    raise Exception("Could not connect to Spark master after 60 seconds")

try:
    # Create SparkSession with Kubernetes configuration
    spark = SparkSession.builder \
        .appName("TailscaleTest") \
        .master("k8s://https://kubernetes.default.svc") \
        .config("spark.kubernetes.container.image", "bitnami/spark:3.5.0") \
        .config("spark.kubernetes.namespace", "datasci") \
        .config("spark.kubernetes.driver.pod.name", "spark-driver") \
        .config("spark.kubernetes.driver.podTemplateFile", "/workspaces/home-ops-upgrade/kubernetes/apps/datasci/spark/driver-pod-template.yaml") \
        .config("spark.kubernetes.driver.serviceAccountName", "spark") \
        .config("spark.kubernetes.executor.serviceAccountName", "spark") \
        .config("spark.executor.instances", "2") \
        .config("spark.executor.memory", "512m") \
        .config("spark.executor.cores", "1") \
        .config("spark.driver.memory", "512m") \
        .config("spark.driver.cores", "1") \
        .config("spark.driver.maxResultSize", "512m") \
        .config("spark.network.timeout", "300s") \
        .config("spark.executor.heartbeatInterval", "30s") \
        .config("spark.rpc.askTimeout", "300s") \
        .config("spark.rpc.lookupTimeout", "300s") \
        .config("spark.driver.host", "spark.istio.local") \
        .config("spark.driver.bindAddress", "0.0.0.0") \
        .config("spark.driver.extraJavaOptions", "-Djava.net.preferIPv4Stack=true") \
        .config("spark.executor.extraJavaOptions", "-Djava.net.preferIPv4Stack=true") \
        .config("spark.driver.extraClassPath", "/opt/bitnami/spark/jars/bcprov-jdk15on-1.70.jar:/opt/bitnami/spark/jars/bcpkix-jdk15on-1.70.jar") \
        .config("spark.executor.extraClassPath", "/opt/bitnami/spark/jars/bcprov-jdk15on-1.70.jar:/opt/bitnami/spark/jars/bcpkix-jdk15on-1.70.jar") \
        .getOrCreate()

    # Print Spark configuration
    print("Spark Configuration:")
    for (key, value) in spark.sparkContext.getConf().getAll():
        print(f"{key}: {value}")

    # Wait for Spark to be ready
    print("\nWaiting for Spark to be ready...")
    time.sleep(10)  # Give some time for the connection to stabilize

    # Try a very simple operation
    print("\nCreating RDD...")
    rdd = spark.sparkContext.parallelize([1, 2, 3, 4, 5])
    print("RDD created successfully")

    print("\nCounting RDD...")
    count = rdd.count()
    print(f"RDD count: {count}")

    print("\nTransforming RDD...")
    doubled = rdd.map(lambda x: x * 2)
    result = doubled.collect()
    print(f"Doubled values: {result}")

except Exception as e:
    print(f"\nError during Spark operations: {str(e)}")
    print("\nFull error details:")
    import traceback
    traceback.print_exc()
finally:
    # Only stop if we're done with everything
    if 'spark' in locals() and spark:
        print("\nStopping Spark session...")
        spark.stop()
