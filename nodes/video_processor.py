from state import ClaimState
from clients.video_indexer_client import real_extract_video


def video_processor(state: ClaimState) -> dict:
    """
    Node 1: extracts transcript, OCR text, and keyframe images from the video.
    Uses real Azure AI Video Indexer instead of mock video.
    """

    print("\n[NODE 1] video_processor running...")

    result = real_extract_video(state["video_path"])

    print(f"  transcript length: {len(result['transcript'])} chars")
    print(f"  OCR length: {len(result['ocr_text'])} chars")
    print(f"  frames extracted: {len(result['frames'])}")

    return {
        "transcript": result["transcript"],
        "ocr_text": result["ocr_text"],
        "frames": result["frames"]
    }