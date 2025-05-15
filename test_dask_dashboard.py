import requests

DASK_DASHBOARD_URL = "https://dask.istio.local"

try:
    # Make a GET request to the Dask dashboard
    # We disable SSL verification because it's using a self-signed certificate
    response = requests.get(DASK_DASHBOARD_URL, verify=False)

    # Check if the request was successful (status code 200-299)
    if response.ok:
        print(f"Successfully connected to Dask dashboard at {DASK_DASHBOARD_URL}")
        print(f"Status Code: {response.status_code}")
        # You can print a small part of the content to verify it's HTML
        # print("Response (first 200 chars):")
        # print(response.text[:200])
    else:
        print(f"Failed to connect to Dask dashboard at {DASK_DASHBOARD_URL}")
        print(f"Status Code: {response.status_code}")
        print(f"Response: {response.text}")

except requests.exceptions.SSLError as e:
    print(f"SSL Error connecting to {DASK_DASHBOARD_URL}: {e}")
    print("This might be expected if the certificate is self-signed and not trusted by your system.")
    print("The script attempted to disable verification, but there might be other SSL issues.")

except requests.exceptions.ConnectionError as e:
    print(f"Connection Error connecting to {DASK_DASHBOARD_URL}: {e}")
    print("Ensure that the Dask dashboard is running and accessible from where you are running this script.")
    print(f"Check if you can resolve dask.istio.local and if it points to the correct IP address ({SVC_GATEWAY_ADDR:-192.168.50.187}).") # Assuming SVC_GATEWAY_ADDR is still relevant

except Exception as e:
    print(f"An unexpected error occurred: {e}")
