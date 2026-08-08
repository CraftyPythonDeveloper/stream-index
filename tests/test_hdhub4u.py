import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, patch
from app.providers.hdhub4u import HDHub4uProvider

@pytest.mark.asyncio
async def test_resolve_final_url_no_redirect():
    client = httpx.AsyncClient()
    provider = HDHub4uProvider(client)
    
    # Mock the send method to return a response without consuming body
    mock_resp = AsyncMock()
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "video/mkv", "content-length": "570169691"}
    
    with patch.object(client, "send", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = mock_resp
        
        final_url = await provider._resolve_final_url("https://example.com/stream.mkv")
        
        # Verify the final URL is unchanged
        assert final_url == "https://example.com/stream.mkv"
        # Verify send was called with stream=True
        mock_send.assert_called_once()
        args, kwargs = mock_send.call_args
        assert kwargs.get("stream") is True
        assert kwargs.get("follow_redirects") is False
        
        # Verify body was NOT consumed (aclose was called)
        mock_resp.aclose.assert_called_once()
        mock_resp.aread.assert_not_called()

@pytest.mark.asyncio
async def test_resolve_final_url_with_redirect():
    client = httpx.AsyncClient()
    provider = HDHub4uProvider(client)
    
    # Mock multiple responses for redirect chain
    mock_resp1 = AsyncMock()
    mock_resp1.status_code = 302
    mock_resp1.headers = {"location": "https://example.com/redirected"}
    
    mock_resp2 = AsyncMock()
    mock_resp2.status_code = 200
    mock_resp2.headers = {"content-type": "video/mp4"}
    
    with patch.object(client, "send", new_callable=AsyncMock) as mock_send:
        mock_send.side_effect = [mock_resp1, mock_resp2]
        
        final_url = await provider._resolve_final_url("https://example.com/start")
        
        # Verify final URL is the redirected target
        assert final_url == "https://example.com/redirected"
        
        # Verify send was called twice with stream=True
        assert mock_send.call_count == 2
        for call in mock_send.call_args_list:
            args, kwargs = call
            assert kwargs.get("stream") is True
            assert kwargs.get("follow_redirects") is False
            
        # Verify aclose was called on both responses to avoid downloading body
        mock_resp1.aclose.assert_called_once()
        mock_resp2.aclose.assert_called_once()
        mock_resp1.aread.assert_not_called()
        mock_resp2.aread.assert_not_called()

@pytest.mark.asyncio
async def test_resolve_final_url_exception_handling():
    client = httpx.AsyncClient()
    provider = HDHub4uProvider(client)
    
    with patch.object(client, "send", new_callable=AsyncMock) as mock_send:
        mock_send.side_effect = Exception("Connection error")
        
        # Should gracefully return the original URL if resolution fails
        final_url = await provider._resolve_final_url("https://example.com/fail")
        assert final_url == "https://example.com/fail"
