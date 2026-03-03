from typing import TYPE_CHECKING, Any, cast

import openai
from openai import Omit
from portkey_ai import AsyncPortkey
from portkey_ai.api_resources import exceptions as portkey_exceptions
from typing_extensions import override

from pipelex import pretty_print
from pipelex.cogt.exceptions import ImgGenGenerationError, SdkTypeError
from pipelex.cogt.image.generated_image import GeneratedImageRawDetails
from pipelex.cogt.image.image_size import ImageSize
from pipelex.cogt.img_gen.img_gen_job import ImgGenJob
from pipelex.cogt.img_gen.img_gen_job_components import Quality
from pipelex.cogt.img_gen.img_gen_worker_abstract import ImgGenWorkerAbstract
from pipelex.cogt.model_backends.model_spec import InferenceModelSpec
from pipelex.cogt.usage.token_category import NbTokensByCategoryDict, TokenCategory
from pipelex.plugins.gateway.gateway_deck import GatewayDeck
from pipelex.plugins.gateway.gateway_factory import GatewayFactory
from pipelex.plugins.openai.openai_img_gen_factory import OpenAIImgGenFactory
from pipelex.reporting.reporting_protocol import ReportingProtocol

if TYPE_CHECKING:
    from portkey_ai.api_resources.types.image_type import ImagesResponse


class GatewayImgGenPkWorker(ImgGenWorkerAbstract):
    def __init__(
        self,
        sdk_instance: Any,
        inference_model: InferenceModelSpec,
        reporting_delegate: ReportingProtocol | None = None,
    ):
        super().__init__(inference_model=inference_model, reporting_delegate=reporting_delegate)

        if not isinstance(sdk_instance, AsyncPortkey):
            msg = f"Provided ImgGen sdk_instance for {self.__class__.__name__} is not of type portkey_ai.AsyncPortkey: it's a '{type(sdk_instance)}'"
            raise SdkTypeError(msg)

        self.portkey_client: AsyncPortkey = sdk_instance

    @override
    async def _gen_image(
        self,
        img_gen_job: ImgGenJob,
    ) -> GeneratedImageRawDetails:
        one_image_list = await self._gen_image_list(img_gen_job=img_gen_job, nb_images=1)
        return one_image_list[0]

    @override
    async def _gen_image_list(
        self,
        img_gen_job: ImgGenJob,
        nb_images: int,
    ) -> list[GeneratedImageRawDetails]:
        image_size, width, height = OpenAIImgGenFactory.image_size_for_gpt_image_1(aspect_ratio=img_gen_job.job_params.aspect_ratio)
        output_format = OpenAIImgGenFactory.output_format_for_gpt_image_1(output_format=img_gen_job.job_params.output_format)
        moderation = OpenAIImgGenFactory.moderation_for_gpt_image_1(is_moderated=img_gen_job.job_params.is_moderated)
        background = OpenAIImgGenFactory.background_for_gpt_image_1(background=img_gen_job.job_params.background)
        quality = OpenAIImgGenFactory.quality_for_gpt_image_1(quality=img_gen_job.job_params.quality or Quality.LOW)
        output_compression = OpenAIImgGenFactory.output_compression_for_gpt_image_1()

        config_id = GatewayDeck.get_config_id(headers=self.inference_model.extra_headers or {})

        # Build optional kwargs, excluding any OpenAI Omit sentinels or None values
        # that should not be sent as string parameters to the Portkey API
        optional_kwargs: dict[str, Any] = {}
        if not isinstance(moderation, Omit):
            optional_kwargs["moderation"] = moderation
        if output_format is not None:
            optional_kwargs["output_format"] = output_format

        pretty_print(self.inference_model, title="Inference model")
        try:
            raw_response = await self.portkey_client.with_options(config=config_id).images.generate(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
                prompt=img_gen_job.img_gen_prompt.positive_text,
                model=self.inference_model.model_id,
                background=background,
                quality=quality,
                size=image_size,
                output_compression=output_compression,
                n=nb_images,
                **optional_kwargs,
            )
            images_response = cast("ImagesResponse", raw_response)
        except portkey_exceptions.APIError as exc:
            error_summary = GatewayFactory.make_error_summary_from_portkey_error(exc)
            msg = f"Image generation service error for model '{self.inference_model.model_id}': {error_summary}"
            raise ImgGenGenerationError(msg) from exc
        except openai.APIError as exc:
            msg = f"Image generation service error for model '{self.inference_model.model_id}': {exc.message}"
            raise ImgGenGenerationError(msg) from exc

        if not images_response.data:
            msg = "No result from Gateway (Portkey images API)"
            raise ImgGenGenerationError(msg)

        response_dict: dict[str, Any] = images_response.model_dump(serialize_as_any=True)

        # Extract usage tokens if available
        if (usage_dict := response_dict.get("usage")) and (img_gen_tokens_usage := img_gen_job.job_report.img_gen_tokens_usage):
            nb_tokens: NbTokensByCategoryDict = {}
            if input_tokens := usage_dict.get("prompt_tokens") or usage_dict.get("input_tokens"):
                nb_tokens[TokenCategory.INPUT] = input_tokens
            if output_tokens := usage_dict.get("completion_tokens") or usage_dict.get("output_tokens"):
                nb_tokens[TokenCategory.OUTPUT] = output_tokens
            img_gen_tokens_usage.nb_tokens_by_category = nb_tokens

        # Extract output_format and size from the response (extra="allow" on ImagesResponse preserves these)
        response_output_format: str | None = response_dict.get("output_format")
        if not response_output_format:
            msg = "No output format received from Gateway"
            raise ImgGenGenerationError(msg)

        size: str | None = response_dict.get("size")
        if not isinstance(size, str):
            msg = f"Size from img gen response is not a string: '{size}'"
            raise ImgGenGenerationError(msg)
        size_split = size.split("x")
        if len(size_split) != 2:
            msg = f"Size from img gen response is not a valid size: '{size}'"
            raise ImgGenGenerationError(msg)
        width_str, height_str = size_split
        width = int(width_str)
        height = int(height_str)

        generated_images: list[GeneratedImageRawDetails] = []
        for image_data in images_response.data:
            base64_str = image_data.b64_json
            if not base64_str:
                msg = "No base64 image data received from Gateway"
                raise ImgGenGenerationError(msg)

            generated_images.append(
                GeneratedImageRawDetails(
                    base64_str=base64_str,
                    size=ImageSize(width=width, height=height),
                    image_format=response_output_format,
                ),
            )
        return generated_images
