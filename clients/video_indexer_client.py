import os
import re
import time
import requests
from dotenv import load_dotenv

load_dotenv()

ACCOUNT_ID = os.getenv("AZURE_VIDEO_INDEXER_ACCOUNT_ID")
LOCATION = os.getenv("AZURE_VIDEO_INDEXER_LOCATION")
ACCESS_TOKEN = os.getenv("AZURE_VIDEO_INDEXER_ACCESS_TOKEN")


def real_extract_video(video_path: str) -> dict:
    """
    Uploads video to Azure AI Video Indexer.
    If video already exists, reuses existing video_id.
    Waits until processing is complete.
    Extracts transcript, OCR text, and Azure keyframe thumbnails.
    """

    if not ACCOUNT_ID:
        raise ValueError("Missing AZURE_VIDEO_INDEXER_ACCOUNT_ID in .env")

    if not LOCATION:
        raise ValueError("Missing AZURE_VIDEO_INDEXER_LOCATION in .env")

    if not ACCESS_TOKEN:
        raise ValueError("Missing AZURE_VIDEO_INDEXER_ACCESS_TOKEN in .env")

    print(f"  [Azure Video Indexer] uploading: {video_path}")

    video_id = upload_video(video_path)
    insights = wait_for_processing(video_id)

    transcript = extract_transcript(insights)
    ocr_text = extract_ocr_text(insights)

    frames = download_keyframe_thumbnails(
        video_id=video_id,
        insights_json=insights,
        max_frames=3
    )

    return {
        "transcript": transcript,
        "ocr_text": ocr_text,
        "frames": frames
    }


def upload_video(video_path: str) -> str:
    """
    Uploads local video file to Azure AI Video Indexer.
    If same video already exists, extracts and reuses existing video_id.
    """

    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video file not found: {video_path}")

    video_name = os.path.basename(video_path)

    url = f"https://api.videoindexer.ai/{LOCATION}/Accounts/{ACCOUNT_ID}/Videos"

    params = {
        "name": video_name,
        "privacy": "Private",
        "language": "en-US",
        "indexingPreset": "Default",
        "streamingPreset": "Default",
        "accessToken": ACCESS_TOKEN
    }

    with open(video_path, "rb") as video_file:
        files = {
            "file": (video_name, video_file, "video/mp4")
        }

        response = requests.post(
            url,
            params=params,
            files=files
        )

    # Azure may reject duplicate uploads.
    # In that case, reuse the existing video_id from the error message.
    if response.status_code == 409:
        try:
            error_data = response.json()
            error_type = error_data.get("ErrorType", "")
            message = error_data.get("Message", "")

            if error_type == "ALREADY_EXISTS":
                match = re.search(r"video id: '([^']+)'", message)

                if match:
                    existing_video_id = match.group(1)
                    print(
                        f"  [Azure Video Indexer] video already exists, "
                        f"reusing video_id={existing_video_id}"
                    )
                    return existing_video_id

        except Exception:
            pass

        print("  [Azure Video Indexer] duplicate upload error")
        print(response.text)
        raise RuntimeError("Video already exists, but existing video_id could not be parsed.")

    if response.status_code >= 400:
        print("  [Azure Video Indexer] upload failed")
        print(response.text)
        raise RuntimeError(f"Azure Video Indexer upload failed with status {response.status_code}")

    data = response.json()
    video_id = data["id"]

    print(f"  [Azure Video Indexer] uploaded video_id={video_id}")

    return video_id


def wait_for_processing(video_id: str) -> dict:
    """
    Waits until Azure AI Video Indexer finishes processing.
    Returns full insights JSON.
    """

    url = (
        f"https://api.videoindexer.ai/{LOCATION}"
        f"/Accounts/{ACCOUNT_ID}"
        f"/Videos/{video_id}/Index"
    )

    params = {
        "accessToken": ACCESS_TOKEN,
        "includeSummarizedInsights": "false"
    }

    # 90 tries × 10 seconds = 15 minutes max wait
    for _ in range(90):
        response = requests.get(url, params=params)

        if response.status_code >= 400:
            print("  [Azure Video Indexer] status check failed")
            print(response.text)
            raise RuntimeError(
                f"Azure Video Indexer status check failed with status {response.status_code}"
            )

        data = response.json()
        state = data.get("state")

        print(f"  [Azure Video Indexer] state={state}")

        if state == "Processed":
            return data

        if state == "Failed":
            raise RuntimeError(f"Azure Video Indexer processing failed: {data}")

        time.sleep(10)

    raise TimeoutError("Azure Video Indexer processing timed out")


def extract_transcript(insights_json: dict) -> str:
    """
    Extract transcript text from Azure Video Indexer response.
    """

    lines = []

    for video in insights_json.get("videos", []):
        insights = video.get("insights", {})
        transcript_items = insights.get("transcript", [])

        for item in transcript_items:
            text = item.get("text")
            if text:
                lines.append(text)

    return " ".join(lines)


def extract_ocr_text(insights_json: dict) -> str:
    """
    Extract OCR text from Azure Video Indexer response.
    """

    lines = []

    for video in insights_json.get("videos", []):
        insights = video.get("insights", {})
        ocr_items = insights.get("ocr", [])

        for item in ocr_items:
            text = item.get("text")
            if text:
                lines.append(text)

    return " ".join(lines)


def get_keyframe_thumbnail_ids(insights_json: dict, max_frames: int = 3) -> list:
    """
    Reads Azure Video Indexer insights JSON and collects keyframe thumbnail IDs.
    """

    thumbnail_ids = []

    for video in insights_json.get("videos", []):
        insights = video.get("insights", {})
        shots = insights.get("shots", [])

        for shot in shots:
            keyframes = shot.get("keyFrames", [])

            for keyframe in keyframes:
                instances = keyframe.get("instances", [])

                for instance in instances:
                    thumbnail_id = instance.get("thumbnailId")

                    if thumbnail_id and thumbnail_id not in thumbnail_ids:
                        thumbnail_ids.append(thumbnail_id)

                    if len(thumbnail_ids) >= max_frames:
                        return thumbnail_ids

    return thumbnail_ids


def download_thumbnail(video_id: str, thumbnail_id: str, output_path: str) -> str:
    """
    Downloads one Azure-generated keyframe thumbnail as JPEG.
    """

    url = (
        f"https://api.videoindexer.ai/{LOCATION}"
        f"/Accounts/{ACCOUNT_ID}"
        f"/Videos/{video_id}"
        f"/Thumbnails/{thumbnail_id}"
    )

    params = {
        "accessToken": ACCESS_TOKEN,
        "format": "Jpeg"
    }

    response = requests.get(url, params=params)

    if response.status_code >= 400:
        print("  [Azure Video Indexer] thumbnail download failed")
        print(response.text)
        raise RuntimeError(
            f"Azure Video Indexer thumbnail download failed with status {response.status_code}"
        )

    with open(output_path, "wb") as image_file:
        image_file.write(response.content)

    return output_path


def download_keyframe_thumbnails(
    video_id: str,
    insights_json: dict,
    max_frames: int = 3
) -> list:
    """
    Downloads Azure-generated keyframe thumbnails.
    Returns list of local image file paths.
    """

    output_dir = os.path.join("azure_keyframes", video_id)
    os.makedirs(output_dir, exist_ok=True)

    thumbnail_ids = get_keyframe_thumbnail_ids(
        insights_json=insights_json,
        max_frames=max_frames
    )

    print(f"  [Azure Video Indexer] keyframe thumbnails found: {len(thumbnail_ids)}")

    frame_paths = []

    for i, thumbnail_id in enumerate(thumbnail_ids, start=1):
        output_path = os.path.join(output_dir, f"azure_keyframe_{i}.jpg")

        downloaded_path = download_thumbnail(
            video_id=video_id,
            thumbnail_id=thumbnail_id,
            output_path=output_path
        )

        frame_paths.append(downloaded_path)

    return frame_paths