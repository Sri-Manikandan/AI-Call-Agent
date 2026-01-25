import os
import json
import base64
import asyncio
import websockets
import sys
import audioop
import wave
import io
import time
from dotenv import load_dotenv
from openai import AsyncOpenAI
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    print("Error: OPENAI_API_KEY is not set.")
    sys.exit(1)

client = AsyncOpenAI(api_key=OPENAI_API_KEY)

# Audio Constants
SAMPLE_RATE = 8000
CHANNELS = 1
SAMPLE_WIDTH = 2  # 16-bit PCM
CHUNK_SIZE = 160  # 20ms of audio at 8kHz
SILENCE_THRESHOLD = 500  # RMS threshold for silence detection (tweak as needed)
SILENCE_DURATION = 1.0   # Seconds of silence to trigger STT
VAD_CHECK_INTERVAL = 0.1 # Checking interval

class AudioBuffer:
    def __init__(self):
        self.buffer = bytearray()
        self.last_speech_time = time.time()
        self.speech_detected = False

    def add_chunk(self, chunk_mulaw):
        # Convert u-law to 16-bit PCM for energy calculation
        # audioop.ulaw2lin returns bytes
        pcm_chunk = audioop.ulaw2lin(chunk_mulaw, SAMPLE_WIDTH)
        self.buffer.extend(pcm_chunk)
        
        # Simple energy-based VAD
        rms = audioop.rms(pcm_chunk, SAMPLE_WIDTH)
        if rms > SILENCE_THRESHOLD:
            self.last_speech_time = time.time()
            self.speech_detected = True

    def should_process(self):
        # Trigger if we have speech and enough silence has passed
        if self.speech_detected and (time.time() - self.last_speech_time > SILENCE_DURATION):
            return True
        return False

    def get_wav_data(self):
        # Write PCM buffer to in-memory WAV
        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, 'wb') as wav_file:
            wav_file.setnchannels(CHANNELS)
            wav_file.setsampwidth(SAMPLE_WIDTH)
            wav_file.setframerate(SAMPLE_RATE)
            wav_file.writeframes(self.buffer)
        return wav_buffer.getvalue()

    def clear(self):
        self.buffer = bytearray()
        self.speech_detected = False

async def transcribe_audio(wav_data):
    """Transcribe audio using OpenAI Whisper."""
    try:
        # Whisper requires a named file-like object
        # We can use a custom class or just name the BytesIO
        file_obj = io.BytesIO(wav_data)
        file_obj.name = "audio.wav"
        
        transcription = await client.audio.transcriptions.create(
            model="whisper-1",
            file=file_obj,
            language="en"
        )
        return transcription.text
    except Exception as e:
        print(f"STT Error: {e}")
        return None

async def generate_response(messages, tools_dict):
    """Generate response using OpenAI Chat Completion with dynamic tools."""
    try:
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            tools=tools_dict if tools_dict else None,
            tool_choice="auto" if tools_dict else None,
        )
        return response
    except Exception as e:
        print(f"LLM Error: {e}")
        return None

async def text_to_speech_stream(text, output_queue):
    """Stream audio from OpenAI TTS."""
    try:
        print(f"Generating TTS for: {text}")
        
        # Use with_streaming_response for streaming support
        async with client.audio.speech.with_streaming_response.create(
            model="tts-1",
            voice="alloy",
            input=text,
            response_format="pcm"  # Raw PCM is 24kHz signed 16-bit little-endian
        ) as response:
            
            INPUT_RATE = 24000
            OUTPUT_RATE = 8000
            
            state = None # For ratecv
            
            async for chunk in response.iter_bytes(chunk_size=1024):
                # chunk is 24kHz PCM
                # Downsample to 8kHz
                converted_fragment, state = audioop.ratecv(
                    chunk, 2, 1, INPUT_RATE, OUTPUT_RATE, state
                )
                # Convert to mu-law
                mulaw_fragment = audioop.lin2ulaw(converted_fragment, 2)
                
                # Send in chunks suitable for Twilio
                for i in range(0, len(mulaw_fragment), 160):
                    output_queue.put_nowait(mulaw_fragment[i:i+160])
                
    except Exception as e:
        print(f"TTS Error: {e}")

async def twilio_handler(websocket, mcp_session, mcp_tools_openai_format):
    print("Twilio connection opened")
    audio_buffer = AudioBuffer()
    audio_queue = asyncio.Queue()
    streamsid = None
    
    # Load config mainly for prompt/greeting, knowing tools come from MCP
    with open("config.json", "r") as f:
        config = json.load(f)
    system_prompt = config['agent']['think']['prompt']
    
    # Initialize conversation history
    messages = [{"role": "system", "content": system_prompt}]
    
    # Send Greeting
    greeting = config['agent'].get('greeting')
    if greeting:
        asyncio.create_task(text_to_speech_stream(greeting, audio_queue))
        messages.append({"role": "assistant", "content": greeting})

    async def sender_task():
        while True:
            chunk = await audio_queue.get()
            if streamsid:
                msg = {
                    "event": "media",
                    "streamSid": streamsid,
                    "media": {
                        "payload": base64.b64encode(chunk).decode("ascii")
                    }
                }
                await websocket.send(json.dumps(msg))

    sender_future = asyncio.create_task(sender_task())

    try:
        async for message in websocket:
            data = json.loads(message)
            event = data.get("event")

            if event == "start":
                streamsid = data['start']['streamSid']
                print(f"Stream started: {streamsid}")
            
            elif event == "media":
                payload = data['media']['payload']
                chunk = base64.b64decode(payload)
                audio_buffer.add_chunk(chunk)
                
                if audio_buffer.should_process():
                    wav_data = audio_buffer.get_wav_data()
                    audio_buffer.clear()
                    
                    # Process in background
                    print("Processing speech...")
                    
                    # 1. STT
                    text = await transcribe_audio(wav_data)
                    if text and text.strip():
                        print(f"User said: {text}")
                        messages.append({"role": "user", "content": text})
                        
                        # 2. LLM
                        llm_response = await generate_response(messages, mcp_tools_openai_format)
                        if llm_response:
                            message = llm_response.choices[0].message
                            messages.append(message) # Add assistant response to history
                            
                            if message.tool_calls:
                                # Handle tool calls via MCP
                                for tool_call in message.tool_calls:
                                    func_name = tool_call.function.name
                                    args = json.loads(tool_call.function.arguments)
                                    print(f"Calling tool via MCP: {func_name} {args}")
                                    
                                    try:
                                        result = await mcp_session.call_tool(func_name, arguments=args)
                                        tool_result_content = result.content[0].text # Assuming TextContent
                                        
                                        # Add tool output to history
                                        messages.append({
                                            "role": "tool",
                                            "tool_call_id": tool_call.id,
                                            "content": tool_result_content
                                        })
                                    except Exception as e:
                                        print(f"MCP Tool Call Error: {e}")
                                        messages.append({
                                            "role": "tool",
                                            "tool_call_id": tool_call.id,
                                            "content": f"Error calling tool: {e}"
                                        })
                                        
                                # Get final response from LLM
                                final_res = await generate_response(messages, mcp_tools_openai_format)
                                final_message = final_res.choices[0].message
                                messages.append(final_message)
                                
                                reply_text = final_message.content
                                await text_to_speech_stream(reply_text, audio_queue)
                            
                            elif message.content:
                                # 3. TTS
                                await text_to_speech_stream(message.content, audio_queue)

            elif event == "stop":
                print("Stream stopped")
                break
                
    except Exception as e:
        print(f"Error: {e}")
    finally:
        sender_future.cancel()

async def main():
    # MCP Server Parameters
    server_params = StdioServerParameters(
        command="uv",
        args=["run", "pharmacy_mcp.py"],
        env=None
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            # Initialize connection
            await session.initialize()
            
            # List available tools
            tools_result = await session.list_tools()
            
            # Convert MCP tools to OpenAI tools format
            openai_tools = []
            for tool in tools_result.tools:
                openai_tools.append({
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.inputSchema
                    }
                })
            
            print(f"Discovered {len(openai_tools)} tools from MCP server.")
            
            print("Starting Combinatorial Agent on port 5000...")
            # We need to pass the session and tools to the handler
            # functools.partial is one way, or a wrapper 
            import functools
            handler = functools.partial(twilio_handler, mcp_session=session, mcp_tools_openai_format=openai_tools)
            
            async with websockets.serve(handler, "localhost", 5000):
                await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())
