# listen for request(post) from whatsapp
# query the database for user's data
# send the user query and user data to the agent
# wait for the agent to respond
# send the agent's response back to whatsapp

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from motor.motor_asyncio import AsyncIOMotorClient
import os
from dotenv import load_dotenv
from contextlib import asynccontextmanager
import httpx
from datetime import datetime

# Load environment variables from .env file
load_dotenv()

# MongoDB Atlas connection string from environment variable
MONGODB_URL = os.getenv("MONGODB_URL")
# Agent service URL from environment variable
AGENT_URL = os.getenv("AGENT_URL")  # e.g., https://agrigpt-backend-agent.onrender.com/chat

# Global variables for MongoDB client and collections
client = None
db = None
users_collection = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager for FastAPI app
    Handles startup and shutdown events
    """
    # Startup: Connect to MongoDB Atlas
    global client, db, users_collection
    client = AsyncIOMotorClient(MONGODB_URL)
    try:
        # Ping MongoDB to verify connection
        await client.admin.command('ping')
        print("Successfully connected to MongoDB Atlas!")
        
        # Set database and collection references AFTER client is initialized
        db = client.agriculture
        users_collection = db.users
        print("Database and collection references set successfully!")
        
    except Exception as e:
        print(f"Failed to connect to MongoDB: {e}")
    
    yield
    
    # Shutdown: Close MongoDB connection
    if client:
        client.close()
        print("MongoDB connection closed")

# Initialize FastAPI app with lifespan handler
app = FastAPI(
    title="WhatsApp Bot Service",
    description="Service to handle WhatsApp messages and interact with AI agent",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.getenv("WHATSAPP_ORIGIN")],
    allow_methods=["GET", "POST"],
)

class WhatsAppRequest(BaseModel):
    """
    Request model for incoming WhatsApp messages
    """
    phoneNumber: str
    message: str

@app.get("/")
async def root():
    """
    Root endpoint - Returns service information and available endpoints
    
    Returns:
        dict: Service status and endpoint information
    """
    return {
        "status": "healthy",
        "service": "WhatsApp Bot Service",
        "version": "1.0.0",
        "endpoints": {
            "root": "GET / (Service info)",
            "health": "GET /health",
            "whatsapp": "POST /whatsapp (Main endpoint)",
            "docs": "GET /docs (Swagger UI)",
            "redoc": "GET /redoc (ReDoc UI)"
        }
    }

@app.get("/health")
async def health_check():
    """
    Health check endpoint - Returns service health status and database connection
    
    Returns:
        dict: Health status of the service and its dependencies
    """
    # Check database connection
    db_status = "disconnected"
    try:
        if client:
            await client.admin.command('ping')
            db_status = "connected"
    except Exception as e:
        print(f"Health check - Database error: {str(e)}")
        db_status = f"error: {str(e)}"
    
    # Check agent service availability (optional quick check)
    agent_status = "unknown"
    if AGENT_URL:
        agent_status = "configured"
    else:
        agent_status = "not configured"
    
    return {
        "status": "healthy",
        "service": "WhatsApp Bot Service",
        "version": "1.0.0",
        "timestamp": datetime.utcnow().isoformat(),
        "dependencies": {
            "database": db_status,
            "agent_service": agent_status
        }
    }

async def query_database(phoneNumber):
    """
    Query MongoDB for user data by phone number
    If user doesn't exist, create a new user with just the phone number
    
    Args:
        phoneNumber: User's phone number
        
    Returns:
        dict: User data from database (with datetime converted to string)
    """
    try:
        # Check if users_collection is initialized
        if users_collection is None:
            raise HTTPException(status_code=500, detail="Database not initialized")
        
        # Search for existing user by phone number
        user_data = await users_collection.find_one({"phoneNumber": phoneNumber})
        
        if user_data:
            # User exists, remove MongoDB's internal _id field
            user_data.pop('_id', None)
            # Convert datetime to ISO string for JSON serialization
            if 'createdAt' in user_data and isinstance(user_data['createdAt'], datetime):
                user_data['createdAt'] = user_data['createdAt'].isoformat()
            print(f"Found existing user with phone number: {phoneNumber}")
            return user_data
        else:
            # User doesn't exist, create new user
            created_at = datetime.utcnow()
            new_user = {
                "phoneNumber": phoneNumber,
                "createdAt": created_at
            }
            
            # Insert new user into database
            await users_collection.insert_one(new_user)
            print(f"Created new user with phone number: {phoneNumber}")
            
            # Return the new user data (without _id and datetime converted to string)
            new_user.pop('_id', None)
            new_user['createdAt'] = created_at.isoformat()
            return new_user
            
    except Exception as e:
        # Handle any database errors
        print(f"Database error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

async def send_to_agent(message, user_data):
    """
    Send user message to external agent service via POST request
    Wait for agent's response and return it
    
    Agent Request Format:
        POST /chat
        {
            "message": "user message here"
        }
    
    Agent Response Format:
        {
            "response": "agent response here"
        }
    
    Args:
        message: User's message/query
        user_data: User's data from database (not sent to agent, kept for future use)
        
    Returns:
        str: Agent's response text or error message if service unavailable
    """
    try:
        # Prepare payload for agent service - only send message
        payload = {
            "message": message
        }
        
        # Use httpx async client to make POST request to agent
        async with httpx.AsyncClient() as http_client:
            response = await http_client.post(
                AGENT_URL,
                json=payload,
                headers={
                    "accept": "application/json",
                    "Content-Type": "application/json"
                },
                timeout=120.0  # 120 second timeout
            )
            
            # Raise exception if request failed
            response.raise_for_status()
            
            # Parse JSON response from agent
            agent_data = response.json()
            
            # Extract and return the 'response' field from agent's JSON
            return agent_data.get("response", "No response from agent")
            
    except httpx.TimeoutException:
        # Handle timeout errors - agent service is taking too long
        print(f"Agent service timeout for user {user_data.get('phoneNumber')}")
        return "Sorry, our service is taking longer than expected. Please try again in a few moments."
        
    except httpx.HTTPStatusError as e:
        # Handle HTTP status errors (4xx, 5xx)
        print(f"Agent service HTTP error: {e.response.status_code} - {str(e)}")
        if e.response.status_code == 405:
            return "Sorry, our AI assistant is currently unavailable. We're working to restore the service. Please try again later."
        elif e.response.status_code >= 500:
            return "Sorry, our AI assistant is experiencing technical difficulties. Please try again in a few minutes."
        else:
            return "Sorry, we're unable to process your request right now. Please try again later."
            
    except httpx.ConnectError:
        # Handle connection errors - service is down or unreachable
        print(f"Agent service connection error - service may be down")
        return "Sorry, our AI assistant is currently offline. We're working to restore the service. Please check back soon."
        
    except httpx.RequestError as e:
        # Handle other request errors
        print(f"Agent service request error: {str(e)}")
        return "Sorry, we're having trouble connecting to our AI assistant. Please try again in a few moments."
        
    except Exception as e:
        # Handle any other unexpected errors
        print(f"Unexpected agent communication error: {str(e)}")
        return "Sorry, something went wrong. Please try again later."

@app.post("/whatsapp")
async def handle_whatsapp_request(req: WhatsAppRequest):
    """
    Main endpoint to handle incoming WhatsApp messages
    
    Flow:
    1. Receive request from WhatsApp
    2. Query database for user data (create if doesn't exist)
    3. Send query and user data to agent
    4. Wait for agent response
    5. Return agent response to WhatsApp
    
    Args:
        req: WhatsAppRequest containing phoneNumber and message
        
    Returns:
        dict: Agent's response (phoneNumber and message)
    """
    # Step 1: Query the database for user's data (creates user if not exists)
    user_data = await query_database(req.phoneNumber)

    # Step 2: Send the user query and user data to the agent
    agent_response = await send_to_agent(req.message, user_data)

    # Step 3: Send the agent's response back to WhatsApp
    # Return the agent's response as-is
    return {"phoneNumber": req.phoneNumber, "message": agent_response}