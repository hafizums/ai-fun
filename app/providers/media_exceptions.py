"""Sanitized media-generation exceptions with fixed public codes."""

from __future__ import annotations


class MediaError(Exception):
    """Base media error with a fixed public message and stable error code."""

    code: str = "MEDIA_REQUEST_FAILED"
    public_message: str = "The media provider request failed."

    def __init__(self, message: str | None = None) -> None:
        self.message = message or self.public_message
        super().__init__(self.message)


class MediaConfigurationError(MediaError):
    code = "MEDIA_NOT_CONFIGURED"
    public_message = "The media provider is not configured."


class MediaAuthenticationError(MediaError):
    code = "MEDIA_AUTHENTICATION_FAILED"
    public_message = "Media provider authentication failed."


class MediaTimeoutError(MediaError):
    code = "MEDIA_TIMEOUT"
    public_message = "The media provider request timed out."


class MediaConnectionError(MediaError):
    code = "MEDIA_CONNECTION_FAILED"
    public_message = "Could not connect to the media provider."


class MediaRequestError(MediaError):
    code = "MEDIA_REQUEST_FAILED"
    public_message = "The media provider request failed."


class MediaInvalidResultError(MediaError):
    code = "MEDIA_INVALID_RESULT"
    public_message = "The media provider returned an invalid result."


class BaseImageDownloadError(MediaError):
    code = "BASE_IMAGE_DOWNLOAD_FAILED"
    public_message = "Failed to download the generated base image."


class BaseImageTooLargeError(MediaError):
    code = "BASE_IMAGE_TOO_LARGE"
    public_message = "The generated base image exceeded the download size limit."


class BaseImageInvalidFileError(MediaError):
    code = "BASE_IMAGE_INVALID_FILE"
    public_message = "The generated base image file is invalid."


class BaseImageInvalidAspectRatioError(MediaError):
    code = "BASE_IMAGE_INVALID_ASPECT_RATIO"
    public_message = "The generated base image does not have a valid 9:16 portrait ratio."


class PromptPackageCorruptedError(MediaError):
    code = "PROMPT_PACKAGE_CORRUPTED"
    public_message = "The stored prompt package is corrupted or incomplete."


class ReferenceUploadTooLargeError(MediaError):
    code = "REFERENCE_UPLOAD_TOO_LARGE"
    public_message = "The reference image upload exceeded the size limit."


class ReferenceImageEmptyError(MediaError):
    code = "REFERENCE_IMAGE_EMPTY"
    public_message = "The reference image upload was empty."


class ReferenceImageInvalidFileError(MediaError):
    code = "REFERENCE_IMAGE_INVALID_FILE"
    public_message = "The reference image file is invalid."


class ReferenceImageTooSmallError(MediaError):
    code = "REFERENCE_IMAGE_TOO_SMALL"
    public_message = "The reference image dimensions are below the minimum."


class ReferenceImageTooLargeError(MediaError):
    code = "REFERENCE_IMAGE_TOO_LARGE"
    public_message = "The reference image exceeds the maximum pixel limit."


class ReferenceImageStorageFailedError(MediaError):
    code = "REFERENCE_IMAGE_STORAGE_FAILED"
    public_message = "Failed to store the reference image."


class BaseImageMissingOrInvalidError(MediaError):
    code = "BASE_IMAGE_MISSING_OR_INVALID"
    public_message = "The base image is missing or invalid."


class ReferenceImageMissingOrInvalidError(MediaError):
    code = "REFERENCE_IMAGE_MISSING_OR_INVALID"
    public_message = "The reference image is missing or invalid."


class EditImageDownloadError(MediaError):
    code = "EDIT_IMAGE_DOWNLOAD_FAILED"
    public_message = "Failed to download the edited image."


class EditImageTooLargeError(MediaError):
    code = "EDIT_IMAGE_TOO_LARGE"
    public_message = "The edited image exceeded the download size limit."


class EditImageInvalidFileError(MediaError):
    code = "EDIT_IMAGE_INVALID_FILE"
    public_message = "The edited image file is invalid."


class EditImageInvalidAspectRatioError(MediaError):
    code = "EDIT_IMAGE_INVALID_ASPECT_RATIO"
    public_message = "The edited image does not have a valid 9:16 portrait ratio."
