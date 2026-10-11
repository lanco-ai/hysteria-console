import type { ChatModel } from './chatApi';

// The chat service lists every model the gateway offers, including image, speech and embedding
// models that cannot hold a conversation. Their ids say what they are.
const NON_CHAT_MODEL = /image|dall-?e|imagen|flux|midjourney|stable-diffusion|video|veo|sora|tts|whisper|speech|transcri|audio|embed|moderation|rerank/i;

export function isChatModel(id: string): boolean {
  return !NON_CHAT_MODEL.test(id);
}

/** Chat models in catalog order, then the rest; the first one is the sensible default. */
export function groupChatModels(models: ChatModel[]): { chat: ChatModel[]; other: ChatModel[] } {
  return { chat: models.filter(item => isChatModel(item.id)), other: models.filter(item => !isChatModel(item.id)) };
}

export function defaultChatModel(models: ChatModel[]): string {
  return models.find(item => isChatModel(item.id))?.id || models[0]?.id || '';
}
