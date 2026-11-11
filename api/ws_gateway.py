"""
api/ws_gateway.py - FastAPI/Starlette Low-Latency Inbound Streaming Gateway
"""
import json
import asyncio
from typing import Dict, Any, List, Set, Optional
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from core.logger import get_logger

logger = get_logger("WebSocketGateway")
ws_router = APIRouter()


class ConnectionManager:
    def __init__(self):
        # Maps campaign_id -> Set of active WebSockets
        self.active_campaign_connections: Dict[str, Set[WebSocket]] = {}
        # Maps session_id -> Set of active WebSockets
        self.active_session_connections: Dict[str, Set[WebSocket]] = {}

    async def connect(self, campaign_id: str, websocket: WebSocket):
        await websocket.accept()
        if campaign_id not in self.active_campaign_connections:
            self.active_campaign_connections[campaign_id] = set()
        self.active_campaign_connections[campaign_id].add(websocket)
        logger.info("Client connected to campaign %s (Total: %d)", campaign_id, len(self.active_campaign_connections[campaign_id]))

    def disconnect(self, campaign_id: str, websocket: WebSocket):
        if campaign_id in self.active_campaign_connections:
            self.active_campaign_connections[campaign_id].discard(websocket)
            if not self.active_campaign_connections[campaign_id]:
                del self.active_campaign_connections[campaign_id]
        logger.info("Client disconnected from campaign %s", campaign_id)

    async def connect_session(self, session_id: str, websocket: WebSocket):
        await websocket.accept()
        if session_id not in self.active_session_connections:
            self.active_session_connections[session_id] = set()
        self.active_session_connections[session_id].add(websocket)
        logger.info("Client connected to session %s", session_id)

    def disconnect_session(self, session_id: str, websocket: WebSocket):
        if session_id in self.active_session_connections:
            self.active_session_connections[session_id].discard(websocket)
            if not self.active_session_connections[session_id]:
                del self.active_session_connections[session_id]
        logger.info("Client disconnected from session %s", session_id)

    async def broadcast_to_campaign(self, campaign_id: str, message: Dict[str, Any]):
        if campaign_id not in self.active_campaign_connections:
            return
        dead_sockets = set()
        for ws in self.active_campaign_connections[campaign_id]:
            try:
                await ws.send_json(message)
            except Exception:
                dead_sockets.add(ws)
        for ws in dead_sockets:
            self.disconnect(campaign_id, ws)

    async def stream_tokens(self, websocket: WebSocket, token_generator):
        """Streams chunk-by-chunk tokens to the client."""
        try:
            async for chunk in token_generator:
                await websocket.send_json({"type": "stream_token", "chunk": chunk})
            await websocket.send_json({"type": "stream_end"})
        except Exception as e:
            logger.error("Error during token streaming: %s", e)


manager = ConnectionManager()


@ws_router.websocket("/ws/session/{session_id}")
async def session_endpoint(websocket: WebSocket, session_id: str):
    await manager.connect_session(session_id, websocket)
    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type", "ping")
            if msg_type == "ping":
                await websocket.send_json({"type": "pong", "session_id": session_id})
            else:
                await websocket.send_json({"type": "session_ack", "received": data})
    except WebSocketDisconnect:
        manager.disconnect_session(session_id, websocket)
    except Exception as e:
        logger.error("WebSocket exception in session %s: %s", session_id, e)
        manager.disconnect_session(session_id, websocket)


@ws_router.websocket("/ws/play/{campaign_id}")
async def play_game_endpoint(websocket: WebSocket, campaign_id: str):
    await manager.connect(campaign_id, websocket)
    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type", "player_action")

            if msg_type == "ping":
                await websocket.send_json({"type": "pong"})
            elif msg_type == "focus_change":
                # Broadcast focus switch to update HUDs across observers
                await manager.broadcast_to_campaign(campaign_id, {
                    "type": "focus_updated",
                    "focus_id": data.get("focus_id")
                })
            elif msg_type == "player_action":
                action_text = data.get("action", "")
                logger.info("Campaign %s received action: %s", campaign_id, action_text)
                await websocket.send_json({"type": "action_received", "action": action_text})
            elif msg_type == "dice_check_reported":
                roll_val = data.get("roll")
                stat = data.get("stat", "General")
                logger.info("Dice roll reported for %s: %s", stat, roll_val)
                await manager.broadcast_to_campaign(campaign_id, {
                    "type": "dice_result",
                    "stat": stat,
                    "roll": roll_val
                })
            elif msg_type == "turn_advance_intent":
                logger.info("Turn advance requested in campaign %s", campaign_id)
                await manager.broadcast_to_campaign(campaign_id, {
                    "type": "turn_advanced",
                    "campaign_id": campaign_id
                })
    except WebSocketDisconnect:
        manager.disconnect(campaign_id, websocket)
    except Exception as e:
        logger.error("WebSocket exception in campaign %s: %s", campaign_id, e)
        manager.disconnect(campaign_id, websocket)
