from clients.video_indexer_client import wait_for_processing, extract_transcript, extract_ocr_text

video_id = "uux4vj2hke"

insights = wait_for_processing(video_id)

print("\n===== TRANSCRIPT =====")
print(extract_transcript(insights))

print("\n===== OCR =====")
print(extract_ocr_text(insights))