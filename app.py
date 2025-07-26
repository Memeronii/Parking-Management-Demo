import os
import cv2
import json
import time
from flask import Flask, render_template, Response, request, jsonify, send_file
import numpy as np
from ultralytics import YOLO


app = Flask(__name__)

# --- Configuration ---
# Path to the video file. Make sure you have a 'videos' folder with your video in it.
VIDEO_PATH = os.path.join('videos', 'Car_Park_Timelapse_Video_Generated.mp4')
parking_spaces = []
yolo_model = YOLO('yolov8s.pt') 

latest_occupancy_statuses = []

@app.route('/save_spaces', methods=['POST'])
def save_spaces():
    global parking_spaces
    data = request.get_json()
    parking_spaces = data.get('spaces', [])
    print(f"[INFO] Received {len(parking_spaces)} parking spaces.")
    return jsonify({'status': 'success', 'count': len(parking_spaces)})

@app.route('/has_spaces')
def has_spaces():
    return jsonify({'has_spaces': bool(parking_spaces)})

@app.route('/first_frame')
def first_frame():
    cap = cv2.VideoCapture(VIDEO_PATH)
    success, frame = cap.read()
    cap.release()
    if not success:
        # Return a blank image if failed
        blank = np.zeros((480, 640, 3), dtype=np.uint8)
        ret, buffer = cv2.imencode('.jpg', blank)
        return Response(buffer.tobytes(), mimetype='image/jpeg')
    ret, buffer = cv2.imencode('.jpg', frame)
    return Response(buffer.tobytes(), mimetype='image/jpeg')

def detect_occupancy_yolo(frame, spaces, results):
    # Use pre-computed YOLO results instead of running detection again
    car_classes = {67}
    car_boxes = [b for b, c in zip(results.boxes.xyxy.cpu().numpy(), results.boxes.cls.cpu().numpy()) if int(c) in car_classes]
    statuses = []
    for box in spaces:
        x, y, w, h = int(box['x']), int(box['y']), int(box['w']), int(box['h'])
        px1, py1, px2, py2 = x, y, x+w, y+h
        occupied = any(
            not (bx2 < px1 or bx1 > px2 or by2 < py1 or by1 > py2)
            for bx1, by1, bx2, by2 in car_boxes
        )
        statuses.append(occupied)
    return statuses

def generate_frames():
    global latest_occupancy_statuses
    """
    Reads the video file, processes each frame, and yields it as a byte stream
    optimized for better performance.
    """
    video_capture = cv2.VideoCapture(VIDEO_PATH)

    if not video_capture.isOpened():
        print(f"[ERROR] Could not open video file: {VIDEO_PATH}")
        return

    # Get the original video's frames per second (FPS)
    fps = video_capture.get(cv2.CAP_PROP_FPS)
    print(f"[INFO] Video stream started. Target FPS: {fps:.2f}")

    frame_count = 0
    while True:
        # Read a frame from the video
        success, frame = video_capture.read()

        if not success:
            # If the video ends, reset to the beginning to loop it
            print("[INFO] End of video reached. Looping...")
            video_capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
            continue

        # Run YOLO on every frame for vehicle tracking (cool visual effect)
        results = yolo_model(frame)[0]
        
        # Draw all YOLO detections for debugging (every frame)
        detected_classes = [(int(cls), results.names[int(cls)]) for cls in results.boxes.cls.cpu().numpy()]
        if detected_classes:
            print(f"[YOLO] Detected classes this frame: {detected_classes}")
        # Update this set to include the class indices YOLO uses for your cars
        car_classes = {67} 
        car_boxes = [b for b, c in zip(results.boxes.xyxy.cpu().numpy(), results.boxes.cls.cpu().numpy()) if int(c) in car_classes]
        for box, cls, conf in zip(results.boxes.xyxy.cpu().numpy(), results.boxes.cls.cpu().numpy(), results.boxes.conf.cpu().numpy()):
            bx1, by1, bx2, by2 = map(int, box)
            # Quick fix: show 'Car' for class 67
            if int(cls) == 67:
                label = f"Car {conf:.2f}"
            else:
                label = f"{results.names[int(cls)]} {conf:.2f}"
            cv2.rectangle(frame, (bx1, by1), (bx2, by2), (255, 200, 0), 2)
            cv2.putText(frame, label, (bx1, by1-10), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255,200,0), 2)

        # Only update occupancy detection every 3 frames for better performance
        if frame_count % 3 == 0:
            # Draw parking spaces and occupancy status
            if parking_spaces:
                statuses = detect_occupancy_yolo(frame, parking_spaces, results)
                latest_occupancy_statuses = statuses # Update global variable
                for box, occupied in zip(parking_spaces, statuses):
                    x, y, w, h = int(box['x']), int(box['y']), int(box['w']), int(box['h'])
                    color = (0, 0, 255) if occupied else (0, 255, 0)  # Red=occupied, Green=vacant
                    cv2.rectangle(frame, (x, y), (x+w, y+h), color, 2)
        else:
            # For frames without occupancy update, just draw the parking spaces with previous status
            if parking_spaces and latest_occupancy_statuses:
                for box, occupied in zip(parking_spaces, latest_occupancy_statuses):
                    x, y, w, h = int(box['x']), int(box['y']), int(box['w']), int(box['h'])
                    color = (0, 0, 255) if occupied else (0, 255, 0)  # Red=occupied, Green=vacant
                    cv2.rectangle(frame, (x, y), (x+w, y+h), color, 2)

        # Encode the frame in JPEG format
        (flag, encoded_image) = cv2.imencode(".jpg", frame)

        # Ensure the frame was successfully encoded
        if not flag:
            continue

        # Yield the output frame in the byte format
        yield(b'--frame\r\n' b'Content-Type: image/jpeg\r\n\r\n' +
              bytearray(encoded_image) + b'\r\n')

        frame_count += 1

    video_capture.release()
    print("[INFO] Video stream stopped.")


@app.route('/')
def index():
    """Video streaming home page."""
    return render_template('index.html')


@app.route('/video_feed')
def video_feed():
    """Video streaming route. Put this in the src attribute of an img tag."""
    return Response(generate_frames(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')
@app.route('/debug_yolo')
def debug_yolo():
    cap = cv2.VideoCapture(VIDEO_PATH)
    success, frame = cap.read()
    cap.release()
    if not success:
        return jsonify({'error': 'Could not read frame'})
    results = yolo_model(frame)[0]
    car_classes = {67}
    car_boxes = [b for b, c in zip(results.boxes.xyxy.cpu().numpy(), results.boxes.cls.cpu().numpy()) if int(c) in car_classes]
    all_classes = [int(c) for c in results.boxes.cls.cpu().numpy()]
    return jsonify({
        'num_cars': len(car_boxes),
        'all_detected_classes': all_classes
    })

@app.route('/current_occupancy')
def current_occupancy():
    return jsonify({'statuses': latest_occupancy_statuses})

if __name__ == '__main__':
    app.run(debug=True)