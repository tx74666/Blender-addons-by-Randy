"""Native image encoding with atomic publication and unchanged source state."""

import os
import tempfile


def save_png_copy(image, path):
    """Encode the current image buffer, including packed/unsaved edits.

    Blender owns color conversion, alpha and precision.  Saving a copy keeps
    the source image's path, packed data and dirty state intact.  Publish only
    after encoding succeeds so a failed save cannot destroy an existing map.
    """
    destination = os.path.abspath(path)
    directory = os.path.dirname(destination)
    os.makedirs(directory, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".rr-image-", suffix=".png", dir=directory)
    os.close(descriptor)
    original_format = getattr(image, "file_format", None)
    original_path = getattr(image, "filepath_raw", None)
    try:
        try:
            if original_format is not None:
                image.file_format = "PNG"
            image.save(filepath=temporary, save_copy=True)
        finally:
            if original_format is not None and image.file_format != original_format:
                image.file_format = original_format
            if original_path is not None and image.filepath_raw != original_path:
                image.filepath_raw = original_path
        if not os.path.isfile(temporary) or os.path.getsize(temporary) == 0:
            raise RuntimeError(f"Image '{image.name}' was not written.")
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)
    return destination
