/** One description of the AI provider for both screens: dot colour, short text, tooltip. */
export function describeAi(aiStatus) {
  if (!aiStatus) return { cls: 'deterministic', text: 'AI —', title: 'AI status unknown' };
  if (aiStatus.groq_selected) {
    return { cls: aiStatus.groq_key_set ? 'ready' : 'down', text: aiStatus.groq_key_set ? 'AI · Groq' : 'AI · key missing',
      title: `${aiStatus.groq_model} on Groq. Used only for the AI diagnosis and Ask OpsLens.` };
  }
  if (aiStatus.local_selected) {
    const ok = aiStatus.reachable && aiStatus.model_installed;
    return { cls: ok ? 'ready' : 'down', text: ok ? 'AI · LM Studio' : 'AI · offline',
      title: aiStatus.error || aiStatus.warning || `${aiStatus.model} in LM Studio on this computer` };
  }
  return { cls: 'deterministic', text: 'AI off', title: 'Answers are built from the record without an AI model' };
}
