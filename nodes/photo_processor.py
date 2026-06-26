from state import ClaimState


def photo_processor(state: ClaimState) -> dict:
    """
    Node 1b: handles photo uploads (no Azure Video Indexer needed).
    Photos go directly into frames[] — same field damage_assessor reads.
    No transcript or OCR since there's no video.
    """
    print("\n[NODE 1b] photo_processor running...")

    photo_path = state.get("video_path", "")  # reusing video_path field for the upload path
    print(f"  photo: {photo_path}")

    return {
        "frames":     [photo_path],
        "transcript": "",
        "ocr_text":   ""
    }
