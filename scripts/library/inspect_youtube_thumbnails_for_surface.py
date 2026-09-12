from concurrent.futures import ThreadPoolExecutor, as_completed
import io
from typing import Dict, List, Optional
import urllib.request
from PIL import ImageFile


def fetch_thumbnail_metadata(
    video_id: str,
    timeout: float = 5.0,
    headers: Optional[Dict[str, str]] = None,
) -> Dict[str, Optional[str]]:
    """Fetch and parse thumbnail metadata with minimal bandwidth via incremental parsing."""
    url = f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg"
    req_headers = headers or {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }

    req = urllib.request.Request(url, headers=req_headers)
    parser = ImageFile.Parser()

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            # Stream in 2KB chunks to extract dimensions/format without reading the entire payload
            while True:
                chunk = resp.read(2048)
                if not chunk:
                    break
                parser.feed(chunk)
                if parser.image:
                    return {
                        "video_id": video_id,
                        "format": parser.image.format,
                        "size": parser.image.size,
                        "error": None,
                    }

        # Fallback if image headers were incomplete
        img = parser.close()
        return {
            "video_id": video_id,
            "format": img.format if img else None,
            "size": img.size if img else None,
            "error": None,
        }
    except Exception as exc:
        return {
            "video_id": video_id,
            "format": None,
            "size": None,
            "error": str(exc),
        }


def inspect_youtube_thumbnails(
    video_ids: List[str],
    max_workers: int = 8,
    timeout: float = 5.0,
) -> List[Dict[str, Optional[str]]]:
    """Inspect YouTube thumbnail headers concurrently across targeted video IDs."""
    results = []
    worker_count = min(max_workers, len(video_ids)) or 1

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        future_to_vid = {
            executor.submit(fetch_thumbnail_metadata, vid, timeout): vid
            for vid in video_ids
        }
        for future in as_completed(future_to_vid):
            results.append(future.result())

    return results


if __name__ == "__main__":
    target_vids = [
        "BcDSHcrvlRM",
        "-Eqm9sJezVo",
        "6HtLDt7t7Q4",
        "FrFSG78UklU",
        "5EluWeZfamA",
    ]

    inspections = inspect_youtube_thumbnails(target_vids)
    for res in inspections:
        if res["error"]:
            print(f"{res['video_id']}: failed {res['error']}")
        else:
            print(f"{res['video_id']}: format={res['format']}, size={res['size']}")