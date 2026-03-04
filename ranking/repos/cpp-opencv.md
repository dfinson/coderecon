# opencv/opencv

| Field | Value |
|-------|-------|
| **URL** | https://github.com/opencv/opencv |
| **License** | Apache-2.0 |
| **Language** | C++ |
| **Scale** | Large (multi-team project) |
| **Category** | Computer vision library |

## Why this repo

- **No single developer knows it all**: Image processing (filtering, transforms,
  color conversion), feature detection (SIFT, ORB, BRISK), object detection
  (cascade classifiers, DNN module), video analysis (optical flow, tracking),
  camera calibration, 3D reconstruction, highgui (display/interaction),
  machine learning (SVM, k-NN, decision trees, DNN inference), CUDA/OpenCL
  acceleration — each a deep, domain-specific subsystem.
- **Well-structured**: Modules under `modules/` with each module having
  `include/`, `src/`, and `test/` directories. Clear module boundaries
  with dependency declarations between them.
- **Rich history**: 35K+ commits, 25+ years of development. Massive
  contributor base. PRs cover algorithm implementations, performance
  tuning, hardware acceleration, and platform support.
- **Permissive**: Apache-2.0 (since OpenCV 4.5.4).

## Structure overview

```
modules/
├── core/                    # Core data structures and utilities
│   ├── include/opencv2/core/
│   │   ├── mat.hpp          # Mat (image matrix) type
│   │   ├── types.hpp        # Basic types (Point, Rect, Size)
│   │   └── utility.hpp      # Utility functions
│   └── src/
│       ├── matrix.cpp       # Mat implementation
│       ├── arithm.cpp       # Arithmetic operations
│       └── parallel.cpp     # Parallel execution
├── imgproc/                 # Image processing
│   └── src/
│       ├── filter.cpp       # Blur, sharpen, edge detection
│       ├── color.cpp        # Color space conversion
│       ├── geometry.cpp     # Geometric transforms
│       └── histogram.cpp    # Histogram operations
├── features2d/              # Feature detection and matching
├── objdetect/               # Object detection
├── dnn/                     # Deep neural network inference
│   └── src/
│       ├── dnn.cpp          # DNN module core
│       ├── layers/          # Layer implementations (~50 types)
│       └── onnx/            # ONNX model import
├── video/                   # Video analysis (optical flow, tracking)
├── calib3d/                 # Camera calibration, 3D reconstruction
├── highgui/                 # Display and user interaction
├── ml/                      # Traditional machine learning
├── photo/                   # Computational photography
├── stitching/               # Image stitching
└── videoio/                 # Video capture and writing
```

## Scale indicators

- ~3,000 C++ header/source files
- ~1M+ lines of code
- Deep module hierarchies (4-5 levels)
- Cross-cutting core types (Mat, InputArray) used everywhere

## Notes

- Some modules use CUDA/OpenCL for GPU acceleration. These code paths
  are structurally complex but codeplane should index the C++ portions
  well. The `contrib` repo (opencv_contrib) is separate and excluded.
