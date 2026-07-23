# coding=utf-8
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, "../../src")

from kagglesdk.models.types.model_api_service import ApiDeleteModelResponse
from kagglesdk.models.types.model_enums import ModelFramework

from kaggle.api.kaggle_api_extended import KaggleApi


def _make_api():
    api = KaggleApi.__new__(KaggleApi)
    api.already_printed_version_warning = True
    return api


def _assert_no_success_message(mock_print, success_phrase):
    for call_args in mock_print.call_args_list:
        printed_str = call_args[0][0]
        if success_phrase in printed_str:
            raise AssertionError(f"Success message was printed on cancellation: {printed_str}")


class TestModelDeleteCli(unittest.TestCase):
    """Tests for model delete CLI wrappers when confirmation is cancelled or approved."""

    def setUp(self):
        self.api = _make_api()
        self.model = "owner/my-model"
        self.model_instance = "owner/my-model/pytorch/default"
        self.model_instance_version = "owner/my-model/pytorch/default/1"

    @patch("builtins.print")
    @patch.object(KaggleApi, "confirmation", return_value=False)
    @patch.object(KaggleApi, "split_model_string", return_value=("owner", "my-model"))
    def test_model_delete_cli_cancelled(self, mock_split, mock_confirmation, mock_print):
        self.api.model_delete_cli(self.model, False)

        mock_confirmation.assert_called_once_with(f"delete the model {self.model}")
        mock_print.assert_any_call("Deletion cancelled")
        _assert_no_success_message(mock_print, "The model was deleted.")

    @patch("builtins.print")
    @patch.object(KaggleApi, "confirmation", return_value=True)
    @patch.object(KaggleApi, "split_model_string", return_value=("owner", "my-model"))
    @patch.object(KaggleApi, "build_kaggle_client")
    def test_model_delete_cli_success(self, mock_build, mock_split, mock_confirmation, mock_print):
        mock_kaggle = MagicMock()
        mock_kaggle.models.model_api_client.delete_model.return_value = ApiDeleteModelResponse()
        mock_build.return_value.__enter__ = MagicMock(return_value=mock_kaggle)
        mock_build.return_value.__exit__ = MagicMock(return_value=False)

        self.api.model_delete_cli(self.model, False)

        mock_kaggle.models.model_api_client.delete_model.assert_called_once()
        mock_print.assert_any_call("The model was deleted.")

    @patch("builtins.print")
    @patch.object(KaggleApi, "confirmation", return_value=False)
    @patch.object(
        KaggleApi,
        "split_model_instance_string",
        return_value=("owner", "my-model", "pytorch", "default"),
    )
    def test_model_instance_delete_cli_cancelled(self, mock_split, mock_confirmation, mock_print):
        self.api.model_instance_delete_cli(self.model_instance, False)

        mock_confirmation.assert_called_once_with(f"delete the variation {self.model_instance}")
        mock_print.assert_any_call("Deletion cancelled")
        _assert_no_success_message(mock_print, "The model instance was deleted.")

    @patch("builtins.print")
    @patch.object(KaggleApi, "confirmation", return_value=True)
    @patch.object(
        KaggleApi,
        "split_model_instance_string",
        return_value=("owner", "my-model", "pytorch", "default"),
    )
    @patch.object(KaggleApi, "lookup_enum", return_value=ModelFramework.MODEL_FRAMEWORK_PY_TORCH)
    @patch.object(KaggleApi, "build_kaggle_client")
    def test_model_instance_delete_cli_success(
        self, mock_build, mock_lookup, mock_split, mock_confirmation, mock_print
    ):
        mock_kaggle = MagicMock()
        mock_kaggle.models.model_api_client.delete_model_instance.return_value = ApiDeleteModelResponse()
        mock_build.return_value.__enter__ = MagicMock(return_value=mock_kaggle)
        mock_build.return_value.__exit__ = MagicMock(return_value=False)

        self.api.model_instance_delete_cli(self.model_instance, False)

        mock_kaggle.models.model_api_client.delete_model_instance.assert_called_once()
        mock_print.assert_any_call("The model instance was deleted.")

    @patch("builtins.print")
    @patch.object(KaggleApi, "confirmation", return_value=False)
    @patch.object(KaggleApi, "validate_model_instance_version_string")
    def test_model_instance_version_delete_cli_cancelled(self, mock_validate, mock_confirmation, mock_print):
        self.api.model_instance_version_delete_cli(self.model_instance_version, False)

        mock_confirmation.assert_called_once_with(f"delete the version {self.model_instance_version}")
        mock_print.assert_any_call("Deletion cancelled")
        _assert_no_success_message(mock_print, "The model instance version was deleted.")

    @patch("builtins.print")
    @patch.object(KaggleApi, "confirmation", return_value=True)
    @patch.object(KaggleApi, "validate_model_instance_version_string")
    @patch.object(KaggleApi, "lookup_enum", return_value=ModelFramework.MODEL_FRAMEWORK_PY_TORCH)
    @patch.object(KaggleApi, "build_kaggle_client")
    def test_model_instance_version_delete_cli_success(
        self, mock_build, mock_lookup, mock_validate, mock_confirmation, mock_print
    ):
        mock_kaggle = MagicMock()
        mock_kaggle.models.model_api_client.delete_model_instance_version.return_value = ApiDeleteModelResponse()
        mock_build.return_value.__enter__ = MagicMock(return_value=mock_kaggle)
        mock_build.return_value.__exit__ = MagicMock(return_value=False)

        self.api.model_instance_version_delete_cli(self.model_instance_version, False)

        mock_kaggle.models.model_api_client.delete_model_instance_version.assert_called_once()
        mock_print.assert_any_call("The model instance version was deleted.")


if __name__ == "__main__":
    unittest.main()
