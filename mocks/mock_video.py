def mock_extract_video(video_path: str) -> dict:
    """
    MOCK — pretends to be Azure Video Indexer.
    In production this would call the real Azure Video Indexer API
    and return real transcript, OCR text, and key frames.
    """
    print(f"  [MOCK video] extracting: {video_path}")

    return {
        "transcript": (
            "Driver says: I was stopped at a red light on Main Street "
            "when another car ran the light and hit my front bumper. "
            "The impact pushed my car forward about two feet."
        ),
        "ocr_text": (
            "License plate visible: ABC-1234. "
            "Street sign in frame: Main St / Oak Ave intersection."
        ),
        "frames": [
            "frame_001.jpg",   # front of car
            "frame_002.jpg",   # damage close-up
            "frame_003.jpg",   # street view
        ]
    }