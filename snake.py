from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from anthropic import Anthropic
import random
import os
import asyncio
import json
import logging
from collections import deque

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Available Claude models:
# Claude 3 Opus     claude-3-opus-20240229
# Claude 3 Sonnet   claude-3-sonnet-20240229
# Claude 3 Haiku    claude-3-haiku-20240307
# Claude 3.5 Sonnet claude-3-5-sonnet-20240620

SNAKE_MODEL = "claude-3-5-sonnet-20240620"

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")

client = Anthropic(api_key="YOUR API KEY")

MAX_HISTORY = 60  # Maximum number of previous states to remember

class SnakeGame:
    def __init__(self, width, height):
        self.width = width
        self.height = height
        self.snake = [(width // 2, height // 2)]
        self.direction = (1, 0)
        self.food = self.generate_food()
        self.message_history = deque(maxlen=MAX_HISTORY)

    def generate_food(self):
        while True:
            food = (random.randint(0, self.width - 1), random.randint(0, self.height - 1))
            if food not in self.snake:
                return food

    def move(self):
        head = self.snake[0]
        new_head = ((head[0] + self.direction[0]) % self.width, (head[1] + self.direction[1]) % self.height)
        
        if new_head in self.snake[1:]:
            return False  # Game over
        
        self.snake.insert(0, new_head)
        
        if new_head == self.food:
            self.food = self.generate_food()
        else:
            self.snake.pop()
        
        return True

    def change_direction(self, new_direction):
        if (new_direction[0] * -1, new_direction[1] * -1) != self.direction:
            self.direction = new_direction

    def get_state(self):
        return {
            "snake": self.snake,
            "food": self.food,
            "width": self.width,
            "height": self.height
        }

    def add_to_history(self, message):
        self.message_history.append(message)

game = SnakeGame(20, 20)

@app.get("/")
async def get():
    with open('templates/index.html', 'r') as f:
        html_content = f.read()
    return HTMLResponse(content=html_content)

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    logger.info("WebSocket connection accepted")
    try:
        while True:
            await handle_next_move(websocket)
    except WebSocketDisconnect:
        logger.info("Client disconnected")

async def handle_next_move(websocket: WebSocket):
    try:
        logger.info("Asking Claude for next move...")

        # Prepare the context with game state and message history
        context = f"You are the snake in a 20x20 Snake game. This is your life. The current game state is: {game.get_state()}. "
        context += "The snake wraps around the board when it reaches the edges. "
        context += "What direction should the snake move next to eat the food and avoid hitting itself? "
        context +="its important to get to the food as fast as possible. Your life depends on it."
        context += "Use the move_snake tool to make your move. You can move up, down, left, or right"
        # context += "Use an emoji to describe your mood as you are in the game. this way mood: the emoji you want to use. JUST the emoji no explanation.\n\n"
        context += "Briefly describe your thought process behind the move you want to make max 20 words.\n\n"
        # context += "Briedly describe how you feel about being a snake in 10 words or less."
        
        if game.message_history:
            context += "Previous moves:\n"
            for msg in game.message_history:
                context += f"- {msg}\n"

        response = client.messages.create(
            model=SNAKE_MODEL,
            max_tokens=1024,
            temperature=0.5,
            tools=[{
                "name": "move_snake",
                "description": "This tool moves the snake in the Snake game in the specified direction. Use this tool when deciding the next move for the snake to avoid obstacles and eat the food. The direction parameter specifies which way the snake should move and can be 'up', 'down', 'left', or 'right'. Ensure that the snake does not move in the opposite direction of its current movement to avoid an immediate collision.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "direction": {
                            "type": "string",
                            "enum": ["up", "down", "left", "right"],
                            "description": "The direction to move the snake"
                        }
                    },
                    "required": ["direction"]
                }
            }],
            tool_choice={"type": "auto"},
            # tool_choice={"type": "any"},
            # tool_choice={"type": "tool", "name": "move_snake"},
            messages=[{
                "role": "user",
                "content": context
            }]
        )

        logger.info(f"Claude's response: {response.content}")

        # Extract thinking and tool use blocks
        thinking_block = next((block for block in response.content if block.type == "text"), None)
        tool_use_block = next((block for block in response.content if block.type == "tool_use"), None)

        if thinking_block:
            logger.info(f"Claude's thought process: {thinking_block.text}")
            await websocket.send_json({"type": "claude_thinking", "thought": thinking_block.text})

        if tool_use_block:
            direction = tool_use_block.input["direction"]
            logger.info(f"Claude decided to move: {direction}")

            # Send tool usage message
            await websocket.send_json({"type": "tool_usage", "direction": direction})

            # Add the move to the game's message history
            game.add_to_history(f"Moved {direction}")

            if direction == "up":
                game.change_direction((0, -1))
            elif direction == "down":
                game.change_direction((0, 1))
            elif direction == "left":
                game.change_direction((-1, 0))
            elif direction == "right":
                game.change_direction((1, 0))

            if not game.move():
                logger.info("Game over")
                await websocket.send_json({"type": "game_over"})
            else:
                game_state = game.get_state()
                logger.info(f"Sending game state: {game_state}")
                await websocket.send_json({"type": "game_state", "state": game_state})

        # Add a delay to slow down the game
        await asyncio.sleep(1)

    except Exception as e:
        logger.error(f"An error occurred: {e}")
        await websocket.send_json({"type": "error", "message": str(e)})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)