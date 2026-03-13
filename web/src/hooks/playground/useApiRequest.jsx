/*
Copyright (C) 2025 QuantumNous

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU Affero General Public License as
published by the Free Software Foundation, either version 3 of the
License, or (at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public License
along with this program. If not, see <https://www.gnu.org/licenses/>.

For commercial licensing, please contact support@quantumnous.com
*/

import { useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { SSE } from 'sse.js';
import { API_ENDPOINTS, MESSAGE_STATUS } from '../../constants/playground.constants';
import {
  getUserIdFromLocalStorage,
  processThinkTags,
  processIncompleteThinkTags,
} from '../../helpers';

export const useApiRequest = (
  setMessage,
  setDebugData,
  setActiveDebugTab,
  sseSourceRef,
  saveMessages,
) => {
  const { t } = useTranslation();

  const applyAutoCollapseLogic = useCallback((message, isThinkingComplete = true) => {
    const shouldAutoCollapse = isThinkingComplete && !message.hasAutoCollapsed;
    return {
      isThinkingComplete,
      hasAutoCollapsed: shouldAutoCollapse || message.hasAutoCollapsed,
      isReasoningExpanded: shouldAutoCollapse ? false : message.isReasoningExpanded,
    };
  }, []);

  const streamMessageUpdate = useCallback(
    (textChunk, type) => {
      setMessage((prevMessage) => {
        const lastMessage = prevMessage[prevMessage.length - 1];
        if (!lastMessage || lastMessage.role !== 'assistant') {
          return prevMessage;
        }
        if (lastMessage.status === MESSAGE_STATUS.ERROR) {
          return prevMessage;
        }
        if (
          lastMessage.status !== MESSAGE_STATUS.LOADING &&
          lastMessage.status !== MESSAGE_STATUS.INCOMPLETE
        ) {
          return prevMessage;
        }

        let newMessage = { ...lastMessage };

        if (type === 'reasoning') {
          newMessage = {
            ...newMessage,
            reasoningContent: (lastMessage.reasoningContent || '') + textChunk,
            status: MESSAGE_STATUS.INCOMPLETE,
            isThinkingComplete: false,
          };
        } else if (type === 'content') {
          const newContent = (lastMessage.content || '') + textChunk;
          let thinkingCompleteFromTags = lastMessage.isThinkingComplete;

          if (lastMessage.isReasoningExpanded && newContent.includes('</think>')) {
            const thinkMatches = newContent.match(/<think>/g);
            const thinkCloseMatches = newContent.match(/<\/think>/g);
            if (
              thinkMatches &&
              thinkCloseMatches &&
              thinkCloseMatches.length >= thinkMatches.length
            ) {
              thinkingCompleteFromTags = true;
            }
          }

          const isThinkingComplete =
            (lastMessage.reasoningContent && !lastMessage.isThinkingComplete) ||
            thinkingCompleteFromTags;

          newMessage = {
            ...newMessage,
            content: newContent,
            status: MESSAGE_STATUS.INCOMPLETE,
            ...applyAutoCollapseLogic(lastMessage, isThinkingComplete),
          };
        }

        return [...prevMessage.slice(0, -1), newMessage];
      });
    },
    [setMessage, applyAutoCollapseLogic],
  );

  const completeMessage = useCallback(
    (status = MESSAGE_STATUS.COMPLETE) => {
      setMessage((prevMessage) => {
        const lastMessage = prevMessage[prevMessage.length - 1];
        if (
          !lastMessage ||
          lastMessage.status === MESSAGE_STATUS.COMPLETE ||
          lastMessage.status === MESSAGE_STATUS.ERROR
        ) {
          return prevMessage;
        }

        const updatedMessages = [
          ...prevMessage.slice(0, -1),
          {
            ...lastMessage,
            status,
            ...applyAutoCollapseLogic(lastMessage, true),
          },
        ];

        if (status === MESSAGE_STATUS.COMPLETE || status === MESSAGE_STATUS.ERROR) {
          setTimeout(() => saveMessages(updatedMessages), 0);
        }

        return updatedMessages;
      });
    },
    [setMessage, applyAutoCollapseLogic, saveMessages],
  );

  const handleNonStreamRequest = useCallback(
    async (payload) => {
      setDebugData?.((prev) => ({
        ...(prev || {}),
        request: payload,
        timestamp: new Date().toISOString(),
        response: null,
        sseMessages: null,
        isStreaming: false,
      }));
      setActiveDebugTab?.('request');

      try {
        const response = await fetch(API_ENDPOINTS.CHAT_COMPLETIONS, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'New-Api-User': getUserIdFromLocalStorage(),
          },
          body: JSON.stringify(payload),
        });

        if (!response.ok) {
          let errorBody = '';
          try {
            errorBody = await response.text();
          } catch {
            errorBody = 'Unable to read upstream error body';
          }
          throw new Error(`HTTP error! status: ${response.status}, body: ${errorBody}`);
        }

        const data = await response.json();
        setDebugData?.((prev) => ({
          ...(prev || {}),
          response: JSON.stringify(data, null, 2),
          isStreaming: false,
        }));
        setActiveDebugTab?.('response');

        if (!data.choices?.[0]) {
          return;
        }

        const choice = data.choices[0];
        const content = choice.message?.content || '';
        const reasoningContent =
          choice.message?.reasoning_content || choice.message?.reasoning || '';
        const processed = processThinkTags(content, reasoningContent);

        setMessage((prevMessage) => {
          const newMessages = [...prevMessage];
          const lastMessage = newMessages[newMessages.length - 1];
          if (lastMessage?.status === MESSAGE_STATUS.LOADING) {
            newMessages[newMessages.length - 1] = {
              ...lastMessage,
              content: processed.content,
              reasoningContent: processed.reasoningContent,
              status: MESSAGE_STATUS.COMPLETE,
              ...applyAutoCollapseLogic(lastMessage, true),
            };
          }
          return newMessages;
        });
      } catch (error) {
        console.error('Non-stream request error:', error);
        setDebugData?.((prev) => ({
          ...(prev || {}),
          response: JSON.stringify(
            {
              error: error.message || 'Unknown error',
              timestamp: new Date().toISOString(),
            },
            null,
            2,
          ),
          isStreaming: false,
        }));
        setActiveDebugTab?.('response');

        setMessage((prevMessage) => {
          const newMessages = [...prevMessage];
          const lastMessage = newMessages[newMessages.length - 1];
          if (lastMessage?.status === MESSAGE_STATUS.LOADING) {
            newMessages[newMessages.length - 1] = {
              ...lastMessage,
              content: t('请求发生错误: ') + error.message,
              status: MESSAGE_STATUS.ERROR,
              ...applyAutoCollapseLogic(lastMessage, true),
            };
          }
          return newMessages;
        });
      }
    },
    [setMessage, setDebugData, setActiveDebugTab, t, applyAutoCollapseLogic],
  );

  const handleSSE = useCallback(
    (payload) => {
      setDebugData?.((prev) => ({
        ...(prev || {}),
        request: payload,
        timestamp: new Date().toISOString(),
        response: null,
        sseMessages: [],
        isStreaming: true,
      }));
      setActiveDebugTab?.('request');

      const source = new SSE(API_ENDPOINTS.CHAT_COMPLETIONS, {
        headers: {
          'Content-Type': 'application/json',
          'New-Api-User': getUserIdFromLocalStorage(),
        },
        method: 'POST',
        payload: JSON.stringify(payload),
      });

      sseSourceRef.current = source;
      let isStreamComplete = false;
      let responseData = '';

      source.addEventListener('message', (event) => {
        if (event.data === '[DONE]') {
          isStreamComplete = true;
          source.close();
          sseSourceRef.current = null;
          setDebugData?.((prev) => ({
            ...(prev || {}),
            response: responseData,
            sseMessages: [...(prev?.sseMessages || []), '[DONE]'],
            isStreaming: false,
          }));
          completeMessage();
          return;
        }

        try {
          const parsed = JSON.parse(event.data);
          responseData += event.data + '\n';
          setDebugData?.((prev) => ({
            ...(prev || {}),
            sseMessages: [...(prev?.sseMessages || []), event.data],
          }));
          setActiveDebugTab?.('response');

          const delta = parsed.choices?.[0]?.delta;
          if (!delta) {
            return;
          }
          if (delta.reasoning_content) {
            streamMessageUpdate(delta.reasoning_content, 'reasoning');
          }
          if (delta.reasoning) {
            streamMessageUpdate(delta.reasoning, 'reasoning');
          }
          if (delta.content) {
            streamMessageUpdate(delta.content, 'content');
          }
        } catch (error) {
          console.error('Failed to parse SSE message:', error);
          setDebugData?.((prev) => ({
            ...(prev || {}),
            response: responseData,
            sseMessages: [...(prev?.sseMessages || []), event.data],
            isStreaming: false,
          }));
          setActiveDebugTab?.('response');
          streamMessageUpdate(t('解析响应数据时发生错误'), 'content');
          completeMessage(MESSAGE_STATUS.ERROR);
        }
      });

      source.addEventListener('error', (event) => {
        if (!isStreamComplete && source.readyState !== 2) {
          console.error('SSE Error:', event);
          const errorMessage = event.data || t('请求发生错误');
          setDebugData?.((prev) => ({
            ...(prev || {}),
            response: responseData,
            isStreaming: false,
          }));
          setActiveDebugTab?.('response');
          streamMessageUpdate(errorMessage, 'content');
          completeMessage(MESSAGE_STATUS.ERROR);
          sseSourceRef.current = null;
          source.close();
        }
      });

      source.addEventListener('readystatechange', (event) => {
        if (
          event.readyState >= 2 &&
          source.status !== undefined &&
          source.status !== 200 &&
          !isStreamComplete
        ) {
          source.close();
          setDebugData?.((prev) => ({
            ...(prev || {}),
            response: responseData,
            isStreaming: false,
          }));
          setActiveDebugTab?.('response');
          streamMessageUpdate(t('连接已断开'), 'content');
          completeMessage(MESSAGE_STATUS.ERROR);
        }
      });

      try {
        source.stream();
      } catch (error) {
        console.error('Failed to start SSE stream:', error);
        setDebugData?.((prev) => ({
          ...(prev || {}),
          response: JSON.stringify(
            {
              error: error.message || 'Stream start failed',
              timestamp: new Date().toISOString(),
            },
            null,
            2,
          ),
          isStreaming: false,
        }));
        setActiveDebugTab?.('response');
        streamMessageUpdate(t('建立连接时发生错误'), 'content');
        completeMessage(MESSAGE_STATUS.ERROR);
      }
    },
    [sseSourceRef, setDebugData, setActiveDebugTab, streamMessageUpdate, completeMessage, t],
  );

  const onStopGenerator = useCallback(() => {
    if (sseSourceRef.current) {
      sseSourceRef.current.close();
      sseSourceRef.current = null;
    }

    setMessage((prevMessage) => {
      if (prevMessage.length === 0) {
        return prevMessage;
      }
      const lastMessage = prevMessage[prevMessage.length - 1];
      if (
        lastMessage.status !== MESSAGE_STATUS.LOADING &&
        lastMessage.status !== MESSAGE_STATUS.INCOMPLETE
      ) {
        return prevMessage;
      }

      const processed = processIncompleteThinkTags(
        lastMessage.content || '',
        lastMessage.reasoningContent || '',
      );

      const updatedMessages = [
        ...prevMessage.slice(0, -1),
        {
          ...lastMessage,
          status: MESSAGE_STATUS.COMPLETE,
          reasoningContent: processed.reasoningContent || null,
          content: processed.content,
          ...applyAutoCollapseLogic(lastMessage, true),
        },
      ];
      setTimeout(() => saveMessages(updatedMessages), 0);
      return updatedMessages;
    });
  }, [sseSourceRef, setMessage, applyAutoCollapseLogic, saveMessages]);

  const sendRequest = useCallback(
    (payload, isStream) => {
      if (isStream) {
        handleSSE(payload);
      } else {
        handleNonStreamRequest(payload);
      }
    },
    [handleSSE, handleNonStreamRequest],
  );

  return {
    sendRequest,
    onStopGenerator,
    streamMessageUpdate,
    completeMessage,
  };
};
