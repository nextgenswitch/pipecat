import asyncio
import datetime
import io
import json
import os
import sys
import wave
import base64

import aiofiles
from dotenv import load_dotenv
from loguru import logger

from pipecat.audio.utils import pcm_to_ulaw, ulaw_to_pcm, create_default_resampler
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import Frame, AudioRawFrame, InputAudioRawFrame, EndFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext
from pipecat.processors.audio.audio_buffer_processor import AudioBufferProcessor
from pipecat.serializers.base_serializer import FrameSerializer, FrameSerializerType
from pipecat.services.cartesia.tts import CartesiaTTSService
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.transports.network.websocket_server import WebsocketServerTransport, WebsocketServerParams

load_dotenv(override=True)

logger.remove(0)
logger.add(sys.stderr, level="DEBUG")


class NextGenSwitchFrameSerializer(FrameSerializer):
    class InputParams:
        def __init__(self, sample_rate: int = 8000):
            self.sample_rate = sample_rate

    def __init__(self, params: InputParams = InputParams()):
        self._params = params
        self._resampler = create_default_resampler()
        self._sample_rate = params.sample_rate

    @property
    def type(self) -> FrameSerializerType:
        return FrameSerializerType.TEXT

    async def setup(self, frame):
        self._sample_rate = frame.audio_in_sample_rate

    async def serialize(self, frame: Frame) -> str | None:
        if not isinstance(frame, AudioRawFrame):
            return None

        ulaw_bytes = await pcm_to_ulaw(
            frame.audio, frame.sample_rate, self._sample_rate, self._resampler
        )
        payload = base64.b64encode(ulaw_bytes).decode("utf-8")
        message = {
                    "event": "media",
                    "media": 
                        {"payload": payload}
                    
                }
        return json.dumps(message)

    async def deserialize(self, data: str) -> Frame | None:
        try:
            message = json.loads(data)
            if message.get("event") == "media":
                ulaw_bytes = base64.b64decode(message["media"]["payload"])
                pcm = await ulaw_to_pcm(
                    ulaw_bytes, self._params.sample_rate, self._params.sample_rate, self._resampler
                )
                return InputAudioRawFrame(audio=pcm, sample_rate=self._params.sample_rate, num_channels=1)
        except Exception as e:
            logger.error(f"Failed to parse input: {e}")
        return None


async def save_audio(server_name: str, audio: bytes, sample_rate: int, num_channels: int):
    if len(audio) > 0:
        filename = f"{server_name}_recording_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.wav"
        with io.BytesIO() as buffer:
            with wave.open(buffer, "wb") as wf:
                wf.setsampwidth(2)
                wf.setnchannels(num_channels)
                wf.setframerate(sample_rate)
                wf.writeframes(audio)
            async with aiofiles.open(filename, "wb") as file:
                await file.write(buffer.getvalue())
        logger.info(f"Merged audio saved to {filename}")
    else:
        logger.info("No audio data to save")


async def main():
    serializer = NextGenSwitchFrameSerializer()

    transport = WebsocketServerTransport(
        host="0.0.0.0",
        port=8765,
        params=WebsocketServerParams(
            serializer=serializer,
            audio_in_enabled=True,
            audio_out_enabled=True,
            vad_analyzer=SileroVADAnalyzer(),
            session_timeout=300,
        )
    )

    llm = OpenAILLMService(api_key=os.getenv("OPENAI_API_KEY"))
    stt = DeepgramSTTService(api_key=os.getenv("DEEPGRAM_API_KEY"), audio_passthrough=True)
    tts = CartesiaTTSService(
        api_key=os.getenv("CARTESIA_API_KEY"),
        voice_id="71a7ad14-091c-4e8e-a314-022ece01c121",
        push_silence_after_stop=True,
    )

    messages = [
        {"role": "system", "content": "You are a polite virtual assistant for a PBX system. Keep responses short."}
    ]
    context = OpenAILLMContext(messages)
    context_aggregator = llm.create_context_aggregator(context)
    audiobuffer = AudioBufferProcessor(user_continuous_stream=True)

    pipeline = Pipeline([
        transport.input(),
        stt,
        context_aggregator.user(),
        llm,
        tts,
        transport.output(),
        audiobuffer,
        context_aggregator.assistant(),
    ])

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            audio_in_sample_rate=8000,
            audio_out_sample_rate=8000,
            allow_interruptions=True,
        ),
    )

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        await audiobuffer.start_recording()
        messages.append({"role": "system", "content": "Welcome to NextGenSwitch. How can I assist you today?"})
        await task.queue_frames([context_aggregator.user().get_context_frame()])

    @transport.event_handler("on_session_timeout")
    async def on_session_timeout(transport, client):
        logger.info(f"Timeout: {client.remote_address}")
        await task.queue_frames([EndFrame()])

    @audiobuffer.event_handler("on_audio_data")
    async def on_audio_data(buffer, audio, sample_rate, num_channels):
        await save_audio("nextgenswitch", audio, sample_rate, num_channels)

    runner = PipelineRunner()
    await runner.run(task)


if __name__ == "__main__":
    asyncio.run(main())
