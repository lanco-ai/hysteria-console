"""The strict response contracts used by both assistants and capability checks."""


def plan_assistant_schema():
    return {
        'type': 'OBJECT',
        'properties': {
            'summary': {'type': 'STRING'},
            'suggestions': {
                'type': 'ARRAY', 'minItems': 1, 'maxItems': 8,
                'items': {
                    'type': 'OBJECT',
                    'properties': {
                        'title': {'type': 'STRING'}, 'notes': {'type': 'STRING'},
                        'quadrant': {'type': 'STRING', 'enum': ['important_urgent', 'important', 'urgent', 'later']},
                        'start_time': {'type': 'STRING'}, 'estimate_minutes': {'type': 'INTEGER'},
                        'reminder_offset_minutes': {'type': 'INTEGER'}, 'reason': {'type': 'STRING'},
                    },
                    'required': ['title', 'notes', 'quadrant', 'start_time', 'estimate_minutes', 'reminder_offset_minutes', 'reason'],
                },
            },
        },
        'required': ['summary', 'suggestions'],
    }


def video_assistant_schema():
    return {
        'type': 'OBJECT',
        'properties': {
            'title': {'type': 'STRING'}, 'rewritten_text': {'type': 'STRING'},
            'style_prompt': {'type': 'STRING'}, 'aspect_ratio': {'type': 'STRING', 'enum': ['9:16', '16:9', '1:1']},
            'shots': {
                'type': 'ARRAY', 'minItems': 1, 'maxItems': 12,
                'items': {
                    'type': 'OBJECT',
                    'properties': {
                        'title': {'type': 'STRING'}, 'script': {'type': 'STRING'},
                        'shot_type': {'type': 'STRING', 'enum': ['特写', '近景', '中景', '全景']},
                        'character': {'type': 'STRING'}, 'scene': {'type': 'STRING'},
                        'duration': {'type': 'INTEGER'}, 'image_prompt': {'type': 'STRING'},
                        'motion_prompt': {'type': 'STRING'}, 'dialogue': {'type': 'STRING'},
                    },
                    'required': ['title', 'script', 'shot_type', 'character', 'scene', 'duration', 'image_prompt', 'motion_prompt', 'dialogue'],
                },
            },
        },
        'required': ['title', 'rewritten_text', 'style_prompt', 'aspect_ratio', 'shots'],
    }


PLAN_ASSISTANT_TEST_PROMPT = (
    '这是服务能力检查。请仅返回符合要求的计划建议 JSON：summary 为“结构化输出检查”，'
    'suggestions 包含一条有效建议，quadrant 使用 important，estimate_minutes 使用 30，'
    'start_time 和 reminder_offset_minutes 使用空时间和 0。不要包含真实用户计划。'
)

VIDEO_ASSISTANT_TEST_PROMPT = (
    '这是服务能力检查。请仅返回符合要求的单镜头分镜 JSON：画幅 16:9，'
    '镜头时长 5 秒，shot_type 使用近景，所有文字字段填写简短有效内容。'
    '不要生成图片或视频，也不要包含真实用户创意。'
)
