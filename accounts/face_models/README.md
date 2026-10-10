# Face models

Both run locally through OpenCV (already a dependency), so photos never leave the server.

| File | Purpose | Source | Licence |
|---|---|---|---|
| `face_detection_yunet_2023mar.onnx` (233 KB, committed) | Finds faces in an uploaded photo: rejects photos with no face, several faces, or a face too small or blurry | [opencv/face_detection_yunet](https://huggingface.co/opencv/face_detection_yunet) | MIT |
| `face_recognition_sface_2021dec.onnx` (39 MB, **not** committed) | Compares a requested new photo with the current card photo, as advice for the staff member reviewing it | [opencv/face_recognition_sface](https://huggingface.co/opencv/face_recognition_sface) | Apache 2.0 |

Download the comparison model with `python manage.py download_face_models` (the Dockerfile does this at build time). The download is checked against a fixed SHA-256, so a changed file is refused.

Face *detection* only locates a face. Face *comparison* creates biometric data, which POPIA treats as special personal information: it runs only when `FACE_COMPARISON_ENABLED` is on and the student has consented, its result only advises a staff member, and nothing biometric is stored.
