import asyncio

async def wait_for_condition(condition, timeout: float, check_interval: float=0.1):
    """
    Waits for a condition to be true with a timeout.

    :param condition: a function that returns a boolean value
    :param timeout: the maximum time to wait in seconds
    :param check_interval: how often to check the condition in seconds. Defaults to 0.1.
    :return: True if condition was met, False if timeout
    """
    start_time = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - start_time < timeout:
        if condition():
            return True
        await asyncio.sleep(check_interval)
    raise TimeoutError(f"Timeout of {timeout} seconds reached while waiting for condition.")
