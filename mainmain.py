import os
import json
import base64
import asyncio
import websockets
import sys
from dotenv import load_dotenv
from pharmacy_functions import FUNCTION_MAP

load_dotenv()

# OpenAI Realtime API Configuration
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    print("Error: OPENAI_API_KEY is not set in environment variables.")
    sys.exit(1)

URL = "wss://api.openai.com/v1/realtime?model=gpt-realtime"

def load_config():
    with open("config.json", "r") as f:
        return json.load(f)

async def send_session_update(sts_ws, config):
    """Send session update to OpenAI with instructions and tools."""
    
    # Map config functions to OpenAI tools format
    tools = []
    for func in config['agent']['think']['functions']:
        tool = {
            "type": "function",
            "name": func["name"],
            "description": func["description"],
            "parameters": func["parameters"]
        }
        tools.append(tool)

    session_update = {
        "type": "session.update",
        "session": {
            "instructions": config['agent']['think']['prompt'],
            "voice": "alloy",  # Default or map from config if possible
            "input_audio_format": "g711_ulaw",
            "output_audio_format": "g711_ulaw",
            "turn_detection": {
                "type": "server_vad"
            },
            "tools": tools,
            "tool_choice": "auto",
        }
    }
    
    print("Sending session update...", json.dumps(session_update, indent=2))
    await sts_ws.send(json.dumps(session_update))

async def execute_function_call(func_name, arguments):
    if func_name in FUNCTION_MAP:
        try:
            print(f"Executing {func_name} with {arguments}")
            result = FUNCTION_MAP[func_name](**arguments)
            return json.dumps(result)
        except Exception as e:
            print(f"Error executing {func_name}: {e}")
            return json.dumps({"error": str(e)})
    else:
        return json.dumps({"error": f"Function {func_name} not found"})

async def sts_receiver(sts_ws, twilio_ws, streamsid_queue):
    """Receive events from OpenAI and send audio to Twilio."""
    print("OpenAI receiver started")
    streamsid = await streamsid_queue.get()
    
    async for message in sts_ws:
        try:
            response = json.loads(message)
            event_type = response.get("type")

            if event_type == "response.audio.delta":
                # Received audio chunk from OpenAI
                audio_payload = response.get("delta")
                if audio_payload:
                    media_message = {
                        "event": "media",
                        "streamSid": streamsid,
                        "media": {
                            "payload": audio_payload
                        }
                    }
                    await twilio_ws.send(json.dumps(media_message))

            elif event_type == "input_audio_buffer.speech_started":
                # Barge-in detected: Clear Twilio buffer
                print("Barge-in detected, clearing Twilio buffer")
                clear_message = {
                    "event": "clear",
                    "streamSid": streamsid
                }
                await twilio_ws.send(json.dumps(clear_message))
                
                # Also cancel current response in OpenAI
                # (The speech_started event usually implies OpenAI stops generation, 
                # but sending 'response.cancel' is explicit if needed. 
                # For server_vad, it handles interruption automatically usually.)

            elif event_type == "response.function_call_arguments.done":
                # Function call requested
                call_id = response.get("call_id")
                func_name = response.get("name")
                arguments_str = response.get("arguments")
                
                print(f"Function call requested: {func_name} ({call_id})")
                
                try:
                    arguments = json.loads(arguments_str)
                    result = await execute_function_call(func_name, arguments)
                except json.JSONDecodeError:
                    print(f"Failed to decode arguments: {arguments_str}")
                    result = json.dumps({"error": "Invalid arguments JSON"})

                # Send execution output back to OpenAI
                function_output_event = {
                    "type": "conversation.item.create",
                    "item": {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": result
                    }
                }
                await sts_ws.send(json.dumps(function_output_event))
                
                # Trigger response generation for the function output
                response_create_event = {
                    "type": "response.create"
                }
                await sts_ws.send(json.dumps(response_create_event))

            elif event_type == "error":
                print(f"OpenAI Error: {response.get('error')}")

        except Exception as e:
            print(f"Error in sts_receiver: {e}")

async def sts_sender(sts_ws, audio_queue):
    """Read from audio queue (Twilio input) and send to OpenAI."""
    print("OpenAI sender started")
    while True:
        try:
            chunk_base64 = await audio_queue.get()
            
            # Send audio append event
            audio_append_event = {
                "type": "input_audio_buffer.append",
                "audio": chunk_base64
            }
            await sts_ws.send(json.dumps(audio_append_event))
        except Exception as e:
            print(f"Error in sts_sender: {e}")
            break

async def twilio_receiver(twilio_ws, audio_queue, streamsid_queue):
    """Receive audio from Twilio and put into queue."""
    print("Twilio receiver started")
    
    async for message in twilio_ws:
        try:
            data = json.loads(message)
            event = data.get("event")

            if event == "start":
                print(f"Media stream started: {data['start']['streamSid']}")
                streamsid_queue.put_nowait(data['start']['streamSid'])
            
            elif event == "media":
                media = data["media"]
                chunk = media["payload"] # This is base64 encoded mulaw
                # OpenAI expects base64 encoded audio in the 'audio' field
                audio_queue.put_nowait(chunk)
                
            elif event == "stop":
                print("Media stream stopped")
                break
                
        except Exception as e:
            print(f"Error in twilio_receiver: {e}")

async def twilio_handler(twilio_ws):
    print("New Twilio connection")
    audio_queue = asyncio.Queue()
    streamsid_queue = asyncio.Queue()

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "openai-beta": "realtime=v1"
    }

    try:
        async with websockets.connect(URL, additional_headers=headers) as sts_ws:
            print("Connected to OpenAI Realtime API")
            
            # Send session configuration
            config = load_config()
            await send_session_update(sts_ws, config)

            # Send greeting
            if config['agent'].get('greeting'):
                greeting_event = {
                    "type": "response.create",
                    "response": {
                        "instructions": f"Please say the following greeting exactly: {config['agent']['greeting']}"
                    }
                }
                print("Sending greeting...")
                await sts_ws.send(json.dumps(greeting_event))

            # Start tasks
            await asyncio.gather(
                sts_receiver(sts_ws, twilio_ws, streamsid_queue),
                sts_sender(sts_ws, audio_queue),
                twilio_receiver(twilio_ws, audio_queue, streamsid_queue)
            )
    except Exception as e:
        print(f"Error in twilio_handler: {e}")
    finally:
        print("Twilio connection closed")

async def main():
    print("Starting server on port 5000...")
    async with websockets.serve(twilio_handler, "localhost", 5000):
        await asyncio.Future()  # run forever

if __name__ == "__main__":
    asyncio.run(main())



