import os
import time
from typing import Any, Generator

from pydantic import BaseModel, validate_arguments

from .request import (
    CreateRequest,
    CreateParameters,
    AnalyseRequest,
    SelectRequest,
    TokenizeRequest,
    Endpoint,
)
from .response import CreateResponse, AnalyseResponse, SelectResponse, TokenizeResponse, ErrorResponse
from .communicator import Communicator


def print_logs(msg, end: str | None = None, verbose: bool = False):
    if verbose:
        print(msg, end=end, flush=True)


class RemoteModel:
    def __init__(
        self,
        base_address: str | None = None,
        headers: dict[str, str] | None = None,
        timeout_s: int = 180,
        verbose: bool = False,
        comm=None,
        raise_for_status: bool = False,
        model_name: str | None = None,
    ) -> None:
        self.verbose = verbose
        assert base_address is not None or comm is not None, "You must provide base_address or comm"
        base_headers = {"Content-Type": "application/json", "Accept": "application/json"} | {
            "X-API-KEY": os.environ.get("PARADIGM_API_KEY", str(None)),
            "X-Model": str(model_name),
        }
        self.comm = comm or Communicator(
            base_address,
            headers or base_headers,
            timeout_s,
            raise_for_status=raise_for_status,
        )
        self._wait_for_model_server()

    def _post(
        self, data: Any, endpoint: Endpoint, num_tasks: int, show_progress: bool = True
    ) -> list[SelectResponse] | list[AnalyseResponse] | list[CreateResponse] | list[TokenizeResponse] | ErrorResponse:

        response = self.comm(data, endpoint, stream=False, **{"num_tasks": num_tasks, "show_progress": show_progress})

        def convert_output(response):
            match endpoint:
                case Endpoint.select:
                    return SelectResponse(**response)
                case Endpoint.analyse:
                    return AnalyseResponse(**response)
                case Endpoint.create:
                    return CreateResponse(**response)
                case Endpoint.tokenize:
                    return TokenizeResponse(**response)

        if "responses" not in response:
            if "detail" in response:
                return ErrorResponse(
                    request_id="", error_msg=response.get("detail"), status_code=response.get("status_code")
                )
            return ErrorResponse(**response)

        outputs = [convert_output(r) for r in response["responses"]]

        return outputs

    def _post_stream(self, data: Any) -> Generator[str, None, None]:
        yield from self.comm(data, Endpoint.stream_create, stream=True)

    def _post_objects(
        self, objects: BaseModel | list[BaseModel], endpoint: Endpoint, show_progress: bool = False
    ) -> list[SelectResponse] | list[AnalyseResponse] | list[CreateResponse] | list[TokenizeResponse] | ErrorResponse:
        def compute_num_tasks(obj) -> int:
            match endpoint:
                case Endpoint.create:
                    return obj.params.n_completions
                case Endpoint.select:
                    return len(obj.candidates)
                case _:
                    return 1

        if isinstance(objects, list):
            num_tasks = sum([compute_num_tasks(obj) for obj in objects])
            data = [obj.dict() for obj in objects]
        else:
            num_tasks = compute_num_tasks(objects)
            data = objects.dict()
        return self._post(data, endpoint, num_tasks=num_tasks, show_progress=show_progress)

    def _get_params(self, params: CreateParameters | None = None, **kwargs) -> dict[str, Any]:
        if params is None:
            params = CreateParameters()
        if kwargs:
            params = params.copy(update=kwargs)
        return params.dict()

    def _format_single_request_output(
        self,
        response: list[CreateResponse]
        | list[AnalyseResponse]
        | list[SelectResponse]
        | list[TokenizeResponse]
        | ErrorResponse,
    ) -> CreateResponse | AnalyseResponse | SelectResponse | TokenizeResponse | ErrorResponse:
        return response if isinstance(response, ErrorResponse) else response[0]

    @validate_arguments
    def create(
        self, prompt: str, params: CreateParameters | None = None, show_progress: bool = False, **kwargs: Any
    ) -> CreateResponse | ErrorResponse:
        params = self._get_params(params, **kwargs)
        response = self._post(
            {"text": prompt, "params": params},
            Endpoint.create,
            num_tasks=params.get("n_completions", 1),
            show_progress=show_progress,
        )
        return self._format_single_request_output(response)

    @validate_arguments
    def stream_create(
        self, prompt: str, params: CreateParameters | None = None, **kwargs: Any
    ) -> Generator[str, None, None]:
        params = self._get_params(params, **kwargs)
        return self._post_stream({"text": prompt, "params": params})

    @validate_arguments
    def analyse(self, text: str, show_progress: bool = False) -> AnalyseResponse | ErrorResponse:
        response = self._post({"text": text}, Endpoint.analyse, num_tasks=1, show_progress=show_progress)
        return self._format_single_request_output(response)

    @validate_arguments
    def select(
        self,
        reference: str,
        candidates: list[str],
        conjunction: str | None = None,
        evaluate_reference: bool = False,
        return_is_greedy_generation: bool = False,
        return_log_probs: bool = False,
        show_progress: bool = False,
    ) -> SelectResponse | ErrorResponse:
        response = self._post(
            {
                "reference": reference,
                "candidates": candidates,
                "conjunction": conjunction,
                "evaluate_reference": evaluate_reference,
                "return_is_greedy_generation": return_is_greedy_generation,
                "return_log_probs": return_log_probs,
            },
            Endpoint.select,
            num_tasks=len(candidates),
            show_progress=show_progress,
        )
        return self._format_single_request_output(response)

    @validate_arguments
    def tokenize(self, text: str, show_progress: bool = False) -> TokenizeResponse | ErrorResponse:
        response = self._post({"text": text}, Endpoint.tokenize, num_tasks=1, show_progress=show_progress)
        return self._format_single_request_output(response)

    @validate_arguments
    def create_from_objects(
        self, create_obj: CreateRequest | list[CreateRequest], show_progress: bool = False
    ) -> list[CreateResponse] | ErrorResponse:
        return self._post_objects(create_obj, Endpoint.create, show_progress=show_progress)

    @validate_arguments
    def analyse_from_objects(
        self, analyse_obj: AnalyseRequest | list[AnalyseRequest], show_progress: bool = False
    ) -> list[AnalyseResponse] | ErrorResponse:
        return self._post_objects(analyse_obj, Endpoint.analyse, show_progress=show_progress)

    @validate_arguments
    def select_from_objects(
        self, select_obj: SelectRequest | list[SelectRequest], show_progress: bool = False
    ) -> list[SelectResponse] | ErrorResponse:
        return self._post_objects(select_obj, Endpoint.select, show_progress=show_progress)

    @validate_arguments
    def tokenize_from_objects(
        self, tokenize_obj: TokenizeRequest | list[TokenizeRequest], show_progress: bool = False
    ) -> list[TokenizeResponse] | ErrorResponse:
        return self._post_objects(tokenize_obj, Endpoint.tokenize, show_progress=show_progress)

    def _wait_for_model_server(self):
        print_logs(f"Waiting for the ModelServer to be ready", end="", verbose=self.verbose)
        counter = 0
        while not self.comm.is_available():
            print_logs(f".", end="", verbose=self.verbose)
            time.sleep(10.0)
            counter += 1
            if counter > 60:
                break
        if self.comm.is_available():
            print_logs(" ModelServer is ready!", verbose=self.verbose)
        else:
            print(
                "We're sorry, but the ModelServer is currently unavailable. Please try again later. If you continue to experience issues, please contact our support team for further assistance. Thank you."
            )
