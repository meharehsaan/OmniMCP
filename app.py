import asyncio
import chainlit as cl

from google import genai
from mcp import ClientSession
from google.genai import types
from utils import setup_logger
from config import DevConfig
from fastapi import Request, Response
from mcp.client.stdio import stdio_client, StdioServerParameters

logging = setup_logger(__name__)

# ---- CONFIG ----
API_KEY = DevConfig.GEMINI_API_KEY
MODEL_ID = DevConfig.GEMINI_MODEL_ID


def mcp_to_genai_tool(mcp_tool):
    """Converts an MCP tool definition to Google GenAI's function declaration format."""
    return types.FunctionDeclaration(name=mcp_tool.name, description=mcp_tool.description, parameters=mcp_tool.inputSchema)


@cl.on_chat_start
async def start():
    """Initializes the connection to the MCP server and Gemini."""
    cl.user_session.set("gemini_history", [])

    msg = cl.Message(content="Kick Starting MCP Server and Gemini...")
    await msg.send()

    try:
        # Use async client
        client_genai = genai.Client(api_key=API_KEY, http_options={'api_version': 'v1alpha'})
        cl.user_session.set("client_genai", client_genai)

        server_params = StdioServerParameters(command="python3", args=["mcp_tools.py"])

        # Create synchronization events for the background task
        shutdown_event = asyncio.Event()
        init_event = asyncio.Event()
        session_state = {}

        async def mcp_runner():
            """A dedicated background task that safely enters and exits the MCP contexts."""
            try:
                async with stdio_client(server_params) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        session_state['session'] = session
                        init_event.set() # Unblock the main start function
                        await shutdown_event.wait() # Wait here until the chat ends
            except Exception as e:
                print(f"MCP Server error: {e}")
                init_event.set()

        # Fire off the runner and wait for it to initialize the session
        asyncio.create_task(mcp_runner())
        await init_event.wait()

        mcp_session = session_state.get('session')
        if not mcp_session:
            raise Exception("Failed to initialize MCP Session.")

        # Store for cleanup later or for use in messages
        cl.user_session.set("mcp_shutdown", shutdown_event)
        cl.user_session.set("mcp_session", mcp_session)

        # Discover tools
        result = await mcp_session.list_tools()
        declarations = [mcp_to_genai_tool(t) for t in result.tools]
        tool_config = types.Tool(function_declarations=declarations)
        cl.user_session.set("tool_config", tool_config)

        msg.content = "Welcome to Ehsaan's MCP Server. How can I help you?"
        await msg.update()
    except Exception as e:
        msg.content = f"Error during initialization: {str(e)}"
        await msg.update()


@cl.on_message
async def main(message: cl.Message):
    gemini_history = cl.user_session.get("gemini_history")
    client_genai = cl.user_session.get("client_genai")
    mcp_session = cl.user_session.get("mcp_session")
    tool_config = cl.user_session.get("tool_config")

    # Add user message to history
    gemini_history.append(types.Content(role="user", parts=[types.Part.from_text(text=message.content)]))

    # Create a message placeholder for streaming
    final_answer = cl.Message(content="")

    try:
        while True:
            # Generate content from Gemini (using async aio client)
            response = await client_genai.aio.models.generate_content(
                model=MODEL_ID,
                contents=gemini_history,
                config=types.GenerateContentConfig(
                    tools=[tool_config],
                    system_instruction="You are a helpful AI assistant with access to local tools. Format your responses nicely using Markdown."
                )
            )

            # Add model's response to history
            model_content = response.candidates[0].content
            gemini_history.append(model_content)

            # Check for function calls
            function_calls = [part.function_call for part in model_content.parts if part.function_call]

            if not function_calls:
                # No more tools to call, send the final text
                if response.text:
                    final_answer.content = response.text
                    await final_answer.send()
                break

            # Execute Tool Calls in parallel
            tool_tasks = []
            tool_names = []

            for fc in function_calls:
                args_dict = fc.args if isinstance(fc.args, dict) else dict(fc.args) if fc.args else {}
                tool_names.append(fc.name)
                
                async def run_tool(name, args):
                    async with cl.Step(name=name, type="tool") as step:
                        step.input = args
                        result = await mcp_session.call_tool(name, args)
                        output_str = str(result.content)
                        step.output = output_str
                        return types.Part.from_function_response(
                            name=name,
                            response={'result': output_str}
                        )
                
                tool_tasks.append(run_tool(fc.name, args_dict))
            
            tool_responses = await asyncio.gather(*tool_tasks)
            
            # Add tool results to history and loop back to model
            gemini_history.append(types.Content(role="tool", parts=tool_responses))

    except Exception as e:
        await cl.Message(content=f"⚠️ Error: {str(e)}").send()


@cl.password_auth_callback
def auth_callback(username: str, password: str):
    # Fetch the user matching username from your database
    # and compare the hashed password with the value stored in the database
    if (username, password) == (DevConfig.USER, DevConfig.PASSWORD):
        return cl.User(
            identifier=DevConfig.USER, metadata={"role": "Admin", "provider": "credentials"}
        )
    else:
        return None


@cl.on_logout
def main(request: Request, response: Response):
    response.delete_cookie("my_cookie")


@cl.on_chat_end
async def end():
    shutdown_event = cl.user_session.get("mcp_shutdown")
    if shutdown_event:
        shutdown_event.set()
