from flask import Flask, render_template, request
import tensorflow as tf
import numpy as np
from tensorflow.keras.preprocessing import image
from tensorflow.keras.applications.efficientnet import preprocess_input
import os

app = Flask(__name__)

# Upload folder
UPLOAD_FOLDER = "static/uploads"
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

# Make sure upload folder exists
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# Load trained model
model = tf.keras.models.load_model("deepfake_detector.keras")


def predict_image(img_path):
    # Load image
    img = image.load_img(img_path, target_size=(224, 224))
    img_array = image.img_to_array(img)

    # Expand dimensions
    img_array = np.expand_dims(img_array, axis=0)

    # 🔥 IMPORTANT: EfficientNet preprocessing
    img_array = preprocess_input(img_array)

    # Predict
    prediction = model.predict(img_array)[0][0]

    print("Raw prediction:", prediction)  # For debugging

    # Your class order was: ['fake', 'real']
    # So:
    # 0 = fake
    # 1 = real

    if prediction > 0.5:
        return "REAL", float(prediction)
    else:
        return "FAKE", float(1 - prediction)


@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        file = request.files["file"]

        if file:
            filepath = os.path.join(app.config["UPLOAD_FOLDER"], file.filename)
            file.save(filepath)

            label, confidence = predict_image(filepath)

            return render_template(
                "index.html",
                filename=file.filename,
                label=label,
                confidence=round(confidence * 100, 2)
            )

    return render_template("index.html")


if __name__ == "__main__":
    app.run(debug=True)