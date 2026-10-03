import { test } from 'node:test';
import assert from 'node:assert/strict';
const { classifyReason } = await import('../profiles/purity-coffee-b2b/index.js');

test('never-rang reasons map to DISPATCH_FAILED-family terminations, not NO_ANSWER', () => {
  assert.equal(classifyReason('AGENT_NOT_JOINED: agent x did not join room r within 45000ms').reason, 'AGENT_NOT_JOINED');
  // legacy final-fail text from before the guard (3 retries of a never-joined agent)
  assert.equal(classifyReason('agent ai-voice-agent-priya did not join room r within 45000ms (last: The operation was aborted due to timeout)').reason, 'AGENT_NOT_JOINED');
  assert.equal(classifyReason('CREDITS_EXHAUSTED: SIP call failed: 402 Insufficient Credits').reason, 'CREDITS_EXHAUSTED');
  assert.equal(classifyReason('SIP call failed: 402 Insufficient Credits (permission_denied)').reason, 'CREDITS_EXHAUSTED');
  const pre = classifyReason('PRE_RING_FAILURE: SIP call failed: 503 Service Unavailable');
  assert.equal(pre.reason, 'PRE_RING_FAILURE');
  assert.equal(pre.sip_status_code, 503);
  assert.equal(classifyReason('', 'dispatch_failed').reason, 'DISPATCH_FAILED');
});

test('rang outcomes are unchanged', () => {
  assert.equal(classifyReason('no-answer (stuck-dispatch sweep)').reason, 'STUCK_DISPATCH');
  assert.equal(classifyReason('SIP call failed: 486 User Busy').reason, 'BUSY');
  assert.equal(classifyReason('SIP call failed: 480 Temporarily Unavailable').reason, 'NO_ANSWER');
  assert.equal(classifyReason('The operation was aborted due to timeout').reason, 'TIMEOUT');
});
