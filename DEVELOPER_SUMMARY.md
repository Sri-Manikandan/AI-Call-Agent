# AI Voice Calling Agent - Developer Summary

## Project Overview
A real-time AI voice agent system designed for pharmacy operations, enabling natural voice interactions over phone calls via Twilio. The agent can handle drug information queries, process prescription orders, and lookup order status through conversational AI.

## Architecture

### High-Level Flow
```
Twilio Call → WebSocket → Python Server → Deepgram Agent API → OpenAI GPT-4o-mini
                    ↓                              ↓
            Audio Streaming              Function Calling (Pharmacy Operations)
```

### Core Components

**1. WebSocket Server** (`main.py`)
- Runs on `localhost:5000`
- Manages bidirectional WebSocket connections between Twilio and Deepgram's Agent API
- Handles real-time audio streaming (μ-law encoding, 8kHz sample rate)

**2. Audio Processing Pipeline**
- **Twilio Receiver**: Ingests incoming audio from Twilio, buffers to 3200 bytes (20 frames * 160 bytes)
- **STS Sender**: Streams buffered audio to Deepgram's Speech-to-Speech (STS) agent
- **STS Receiver**: Receives synthesized speech from Deepgram and forwards to Twilio

**3. Function Calling System** (`pharmacy_functions.py`)
- Exposes three pharmacy operations as callable functions:
  - `get_drug_info(drug_name)` - Drug information lookup
  - `place_order(customer_name, drug_name)` - Order placement
  - `lookup_order(order_id)` - Order status checking
- In-memory storage for orders and drug catalog (10 drugs in DB)

**4. Barge-in Handling**
- Detects when user starts speaking (`UserStartedSpeaking` event)
- Sends `clear` command to Twilio to interrupt agent speech

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Language | Python 3.12+ |
| WebSockets | `websockets` library |
| AI Model | OpenAI GPT-4o-mini |
| STT Provider | Deepgram Nova-3 |
| TTS Provider | Deepgram Aura-2 Thalia (en) |
| Telephony | Twilio Media Streams |
| Package Manager | `uv` |
| Environment | `python-dotenv` |

## Key Features

### 1. Natural Language Understanding
- Contextual conversation handling via GPT-4o-mini
- System prompt configured for professional pharmacy assistant behavior
- Function calling capabilities integrated into LLM

### 2. Voice Processing
- Real-time STT with Deepgram Nova-3
- Natural TTS with Deepgram Aura-2
- Optimized for telephony (μ-law codec, 8kHz)

### 3. Barge-in Support
- User can interrupt agent mid-speech
- Stream clearing mechanism for natural conversation flow

### 4. Function Execution
- Async function call handling
- Structured responses back to AI agent
- Error handling and fallback mechanisms

## Configuration

### Audio Settings (`config.json`)
```json
{
  "encoding": "mulaw",
  "sample_rate": 8000
}
```

### Agent Configuration
- **Language**: English
- **Model**: GPT-4o-mini (temperature: 0.7)
- **System Prompt**: Emphasizes thorough information collection and confirmation
- **Functions**: Exposed as OpenAI function calling schema

### Environment Variables
```bash
DEEPGRAM_API_KEY=your_api_key_here
```

## File Structure

```
AI Voice Calling Agent/
├── main.py                    # Primary server with WebSocket handlers
├── mainmain.py                # Alternate/backup implementation
├── pharmacy_functions.py      # Function implementations + FUNCTION_MAP
├── pharmacy_function.py       # (Likely duplicate)
├── pharmacy_mcp.py            # MCP-related implementation
├── combinatorial_agent.py     # Additional agent logic
├── config.json                # Agent & audio configuration
├── pyproject.toml             # Project dependencies
├── uv.lock                    # Lock file for uv package manager
├── .env                       # API keys (not tracked)
└── README.md                  # Project documentation
```

## Data Models

### Drug Database
```python
{
  "drug_name": {
    "name": str,           # Full pharmaceutical name
    "price": float,        # Unit price
    "description": str,    # Medical description
    "quantity": int        # Standard order quantity
  }
}
```

### Order Model
```python
{
  "id": int,
  "customer": str,
  "drug": str,
  "quantity": int,
  "total": float,
  "status": str          # "pending", etc.
}
```

## Development Workflow

### Setup
```bash
# Install dependencies
uv sync

# Configure environment
echo "DEEPGRAM_API_KEY=your_key" > .env

# Run server
python main.py
```

### Twilio Integration
Configure Twilio phone number with WebSocket URL:
```
wss://your-server.com/
```

## Key Implementation Details

### Async Concurrency
Three concurrent tasks handle real-time communication:
1. `twilio_receiver` - Ingests audio from phone call
2. `sts_sender` - Streams audio to Deepgram
3. `sts_receiver` - Receives AI responses and forwards to Twilio

### Function Call Flow
```
1. User speech → Deepgram STT
2. Text → GPT-4o-mini
3. AI decides to call function
4. FunctionCallRequest event received
5. Python executes function from FUNCTION_MAP
6. FunctionCallResponse sent back to Deepgram
7. AI incorporates result into response
8. Response → Deepgram TTS → User
```

### Error Handling
- WebSocket connection errors caught and logged
- Function call exceptions wrapped in error responses
- Invalid function names return structured error objects

## Limitations & Considerations

1. **In-Memory Storage**: Orders/DB reset on server restart
2. **Single Server**: No horizontal scaling (WebSocket state)
3. **No Authentication**: Function calls execute without verification
4. **Basic Drug DB**: Only 10 drugs supported
5. **No Persistence**: Order history not saved to disk/database

## Future Enhancements

- [ ] Database integration (PostgreSQL/MongoDB)
- [ ] Multi-tenant support with authentication
- [ ] Enhanced drug catalog with inventory management
- [ ] Order history and analytics
- [ ] Integration with pharmacy management systems
- [ ] Load balancing for concurrent calls
- [ ] Call recording and transcription storage

## API Reference

### WebSocket Events (Twilio → Server)
- `connected` - Initial connection
- `start` - Stream started, includes `streamSid`
- `media` - Audio chunk (base64-encoded μ-law)
- `stop` - Stream ended

### WebSocket Events (Server → Twilio)
- `media` - Audio payload to play
- `clear` - Clear audio buffer (barge-in)

### Deepgram Agent Events
- `FunctionCallRequest` - AI wants to execute function
- `FunctionCallResponse` - Result of function execution
- `UserStartedSpeaking` - Barge-in detection

## License & Contact
Not specified in repository.
