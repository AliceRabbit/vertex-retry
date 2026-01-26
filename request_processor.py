"""请求处理模块"""
import json
import logging
from typing import Any, Dict, List, Tuple, Union

from fastapi import Request

logger = logging.getLogger(__name__)


def clean_undefined_values(obj: Union[Dict, List]) -> Any:
    """递归清理对象中的 [undefined] 字符串值和 None 值"""
    if isinstance(obj, dict):
        # 递归清理值
        return {
            k: clean_undefined_values(v) if isinstance(v, (dict, list)) else v
            for k, v in obj.items()
            if v != "[undefined]" and v is not None
        }
    elif isinstance(obj, list):
        # 使用列表推导式过滤
        return [
            clean_undefined_values(item) if isinstance(item, (dict, list)) else item
            for item in obj
            if item != "[undefined]" and item is not None
        ]
    return obj


def should_filter_thinking_config(path: str, json_data: Dict) -> bool:
    """判断是否需要过滤 thinkingConfig"""
    # 快速路径检查
    if "gemini-3-pro-image-preview" in path:
        return True
    
    # 检查模型名称
    if isinstance(json_data, dict):
        model = json_data.get("model")
        if model == "gemini-3-pro-image-preview":
            return True
            
    return False


def remove_key_recursive(obj: Any, key_to_remove: str) -> bool:
    """递归删除指定 key，返回是否有修改"""
    modified = False
    if isinstance(obj, dict):
        if key_to_remove in obj:
            del obj[key_to_remove]
            modified = True

        for value in obj.values():
            if isinstance(value, (dict, list)):
                if remove_key_recursive(value, key_to_remove):
                    modified = True
    elif isinstance(obj, list):
        for item in obj:
            if isinstance(item, (dict, list)):
                if remove_key_recursive(item, key_to_remove):
                    modified = True
    return modified


def filter_thinking_config(json_data: Dict) -> Tuple[Dict, bool]:
    """过滤掉 thinkingConfig 参数"""
    modified = remove_key_recursive(json_data, "thinkingConfig")
    return json_data, modified


class RequestProcessor:
    """请求处理器命名空间"""
    
    @staticmethod
    async def process_body(request: Request, path: str) -> bytes:
        """处理和清理请求体"""
        body = await request.body()
        
        # 快速检查：如果不是 JSON 或为空，直接返回
        content_type = request.headers.get("content-type", "")
        if "application/json" not in content_type or not body:
            return body

        if b"thinkingConfig" not in body and b"[undefined]" not in body:
            return body
        
        try:
            json_data = json.loads(body)
            modified = False
            
            # 1. 清理值 (重建对象结构比 inplace delete 更干净)
            # 注意：clean_undefined_values 现在返回新对象
            new_json_data = clean_undefined_values(json_data)
            
            if new_json_data != json_data:
                json_data = new_json_data
                modified = True
            
            # 2. 过滤 thinkingConfig
            if should_filter_thinking_config(path, json_data):
                json_data, thinking_modified = filter_thinking_config(json_data)
                modified = modified or thinking_modified
            
            if modified:
                logger.debug(f"Modified request body for {path}")
                return json.dumps(json_data).encode('utf-8')
                
        except json.JSONDecodeError:
            logger.warning("Failed to parse JSON body, forwarding raw body")
        except Exception as e:
            logger.error(f"Error processing body: {e}", exc_info=True)
        
        return body
