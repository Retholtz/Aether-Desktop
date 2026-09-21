import concurrent.futures
import io
import urllib.request
from typing import Dict, List, Optional, Tuple
from PIL import Image

DEFAULT_URLS = [
    "https://i.ytimg.com/vi/5p-OZ-jXDbU/maxresdefault.jpg",
    "https://i.ytimg.com/vi/-Eqm9sJezVo/maxresdefault.jpg",
    "https://i.ytimg.com/vi/6HtLDt7t7Q4/maxresdefault.jpg",
    "https://rbkgames.com/wp-content/uploads/2026/09/rhino-srv-elite-dangerous-surface-mining.webp",
]

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}


def fetch_and_inspect_image(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 5.0,
) -> Tuple[str, bool, Optional[str], Optional[Tuple[int, int]], Optional[str]]:
    """Fetches image data concurrently and inspects format and size deterministically."""
    req_headers = headers if headers is not None else DEFAULT_HEADERS
    req = urllib.request.Request(url, headers=req_headers)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            payload = response.read()

        with io.BytesIO(payload) as buffer:
            with Image.open(buffer) as img:
                img_format = img.format
                img_size = img.size

        return (url, True, img_format, img_size, None)
    except Exception as exc:
        return (url, False, None, None, str(exc))


def batch_verify_images(
    urls: List[str] = DEFAULT_URLS,
    max_workers: int = 4,
    timeout: float = 5.0,
) -> List[Tuple[str, bool, Optional[str], Optional[Tuple[int, int]], Optional[str]]]:
    """Executes parallel validation across target image URLs to eliminate latency bottlenecks."""
    results = []
    worker_count = min(len(urls), max_workers) if urls else 1

    with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
        future_map = {
            executor.submit(fetch_and_inspect_image, url, timeout=timeout): url
            for url in urls
        }
        for future in concurrent.futures.as_completed(future_map):
            result = future.result()
            results.append(result)
            url, success, fmt, size, err = result
            if success:
                print(f"Success: {url} {fmt} {size}")
            else:
                print(f"Failed: {url} {err}")

    return results


if __name__ == "__main__":
    batch_verify_images()