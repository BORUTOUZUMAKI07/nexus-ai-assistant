# Nexus AI Assistant — User Guide

## 1. Getting Started
1. Launch the application:
   - Backend: `uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --reload`
   - Frontend: `npm run dev` in `frontend/`
2. Open your browser at `http://localhost:3000`.
3. Click **Sign Up** to create your account or sign in with your credentials.

## 2. Interactive Chat & Streaming
- **Real-Time Streaming**: Responses stream in real-time via Server-Sent Events (SSE).
- **Thinking / CoT Blocks**: Expandable reasoning sections displaying the agent's internal thought chain.
- **Web Mode Toggle**: Enable the globe icon to trigger live Firecrawl internet search.
- **Code Execution Toggle**: Enable the terminal icon to automatically execute Python scripts in an isolated E2B microVM sandbox.

## 3. Knowledge Base & Document RAG
- Navigate to the **Knowledge** tab.
- Drag and drop PDF, Markdown, text, or JSON files.
- Nexus automatically extracts text, performs semantic chunking, and indexes dense + sparse embeddings into Qdrant.
- Ask questions in chat about uploaded documents; answers will include verified numbered citations with source file links.

## 4. Human-In-The-Loop (HITL) Tool Approvals
- Sensitive tools (such as destructive operations or shell commands) trigger an interactive approval prompt in the chat feed.
- Click **Approve** to execute the action or **Deny** to cancel and instruct the agent to take an alternative approach.

## 5. BYOK (Bring Your Own Key) & Usage Monitoring
- Navigate to the **Settings** tab to input your personal Groq, OpenRouter, or OpenAI API keys. Keys are encrypted at rest with AES-256 Fernet.
- Navigate to the **Usage** tab to view your token consumption, per-model costs, and free-tier quota tracking.
