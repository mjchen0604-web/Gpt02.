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

import React, { useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Layout, Toast } from '@douyinfe/semi-ui';
import { UserContext } from '../../context/User';
import { useIsMobile } from '../../hooks/common/useIsMobile';
import { usePlaygroundState } from '../../hooks/playground/usePlaygroundState';
import { useMessageActions } from '../../hooks/playground/useMessageActions';
import { useApiRequest } from '../../hooks/playground/useApiRequest';
import { useMessageEdit } from '../../hooks/playground/useMessageEdit';
import { useDataLoader } from '../../hooks/playground/useDataLoader';
import { useSyncMessageAndCustomBody } from '../../hooks/playground/useSyncMessageAndCustomBody';
import { MESSAGE_ROLES } from '../../constants/playground.constants';
import {
  API,
  buildApiPayload,
  buildMessageContent,
  createLoadingAssistantMessage,
  createMessage,
  encodeToBase64,
  getLogo,
  getTextContent,
  showError,
  showSuccess,
  stringToColor,
} from '../../helpers';
import {
  OptimizedDebugPanel,
  OptimizedMessageActions,
  OptimizedMessageContent,
  OptimizedSettingsPanel,
} from '../../components/playground/OptimizedComponents';
import ChatArea from '../../components/playground/ChatArea';
import FloatingButtons from '../../components/playground/FloatingButtons';
import { PlaygroundProvider } from '../../contexts/PlaygroundContext';

const generateAvatarDataUrl = (username) => {
  if (!username) {
    return 'https://lf3-static.bytednsdoc.com/obj/eden-cn/ptlz_zlp/ljhwZthlaukjlkulzlp/docs-icon.png';
  }
  const firstLetter = username[0].toUpperCase();
  const bgColor = stringToColor(username);
  const svg = `
    <svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 32 32">
      <circle cx="16" cy="16" r="16" fill="${bgColor}" />
      <text x="50%" y="50%" dominant-baseline="central" text-anchor="middle" font-size="16" fill="#ffffff" font-family="sans-serif">${firstLetter}</text>
    </svg>
  `;
  return `data:image/svg+xml;base64,${encodeToBase64(svg)}`;
};

const DEFAULT_DEBUG_DATA = {
  request: null,
  response: null,
  timestamp: null,
  previewRequest: null,
  previewTimestamp: null,
  runtimeDefaultsPreview: null,
  sseMessages: null,
  isStreaming: false,
};

const canUseVisibility = (visibility, isAdminUser) => {
  if (visibility === 'global') return true;
  if (visibility === 'admin') return isAdminUser;
  return false;
};

const Playground = () => {
  const { t } = useTranslation();
  const [userState] = useContext(UserContext);
  const isMobile = useIsMobile();
  const styleState = { isMobile };
  const [searchParams] = useSearchParams();

  const [showDebugPanel, setShowDebugPanel] = useState(false);
  const [customRequestMode, setCustomRequestMode] = useState(false);
  const [customRequestBody, setCustomRequestBody] = useState('');
  const [debugData, setDebugData] = useState(DEFAULT_DEBUG_DATA);
  const [activeDebugTab, setActiveDebugTab] = useState('preview');
  const [previewPayload, setPreviewPayload] = useState(null);
  const [applyToRealAPI, setApplyToRealAPI] = useState(false);
  const [featureFlags, setFeatureFlags] = useState({
    debugVisibility: 'off',
    customRequestVisibility: 'off',
  });

  const state = usePlaygroundState();
  const {
    inputs,
    parameterEnabled,
    systemPrompt,
    showSettings,
    models,
    groups,
    message,
    sseSourceRef,
    chatRef,
    handleInputChange,
    handleParameterToggle,
    debouncedSaveConfig,
    saveMessagesImmediately,
    handleConfigImport,
    handleConfigReset,
    applyRemoteDefaults,
    setShowSettings,
    setModels,
    setGroups,
    setMessage,
  } = state;

  const { sendRequest, onStopGenerator } = useApiRequest(
    setMessage,
    setDebugData,
    setActiveDebugTab,
    sseSourceRef,
    saveMessagesImmediately,
  );

  const isAdminUser = Number(userState?.user?.role || 0) >= 10;
  const canUseDebugPanel = canUseVisibility(featureFlags.debugVisibility, isAdminUser);
  const canUseCustomRequest = canUseVisibility(
    featureFlags.customRequestVisibility,
    isAdminUser,
  );

  const loadPlaygroundConfig = useCallback(async () => {
    try {
      const res = await API.get('/api/playground/config');
      const { success, data, message: apiMessage } = res.data || {};
      if (!success) {
        throw new Error(apiMessage || 'failed to load playground config');
      }
      applyRemoteDefaults(data || {}, isAdminUser);
      setApplyToRealAPI(Boolean(data?.applyToRealAPI));
      setDebugData((prev) => ({
        ...(prev || DEFAULT_DEBUG_DATA),
        runtimeDefaultsPreview: data?.runtimeDefaultsPreview
          ? JSON.stringify(data.runtimeDefaultsPreview, null, 2)
          : null,
      }));
      setFeatureFlags({
        debugVisibility: data?.debugVisibility || 'off',
        customRequestVisibility: data?.customRequestVisibility || 'off',
      });
    } catch (error) {
      showError(error);
    }
  }, [applyRemoteDefaults, isAdminUser]);

  const saveScopedDefaults = useCallback(
    async (scope) => {
      try {
        const res = await API.post('/api/playground/config/defaults', {
          scope,
          config: {
            inputs,
            parameterEnabled,
            systemPrompt,
          },
        });
        if (!res.data?.success) {
          throw new Error(res.data?.message || 'save failed');
        }
        showSuccess(
          scope === 'admin' ? '已保存为管理员默认' : '已保存为全局默认',
        );
        await loadPlaygroundConfig();
      } catch (error) {
        showError(error);
      }
    },
    [inputs, parameterEnabled, systemPrompt, loadPlaygroundConfig],
  );

  const savePersonalDefaults = useCallback(async () => {
    const res = await API.post('/api/playground/config/defaults', {
      scope: 'personal',
      config: {
        inputs,
        parameterEnabled,
        systemPrompt,
      },
    });
    if (!res.data?.success) {
      throw new Error(res.data?.message || 'save failed');
    }
    await loadPlaygroundConfig();
  }, [inputs, parameterEnabled, systemPrompt, loadPlaygroundConfig]);

  const saveApplyToRealAPI = useCallback(
    async (enabled) => {
      try {
        if (enabled) {
          await savePersonalDefaults();
        }
        const res = await API.post('/api/playground/config/apply', {
          enabled,
        });
        if (!res.data?.success) {
          throw new Error(res.data?.message || 'save failed');
        }
        setApplyToRealAPI(enabled);
        showSuccess(enabled ? '真实 API 默认注入已开启' : '真实 API 默认注入已关闭');
      } catch (error) {
        showError(error);
      }
    },
    [savePersonalDefaults],
  );

  const saveVisibility = useCallback(
    async (key, visibility) => {
      try {
        const res = await API.post('/api/playground/config/visibility', {
          key,
          visibility,
        });
        if (!res.data?.success) {
          throw new Error(res.data?.message || 'save failed');
        }
        setFeatureFlags((prev) => ({
          ...prev,
          [key === 'debug' ? 'debugVisibility' : 'customRequestVisibility']:
            visibility,
        }));
        showSuccess('可见性已更新');
      } catch (error) {
        showError(error);
      }
    },
    [],
  );

  useDataLoader(userState, inputs, handleInputChange, setModels, setGroups);

  useEffect(() => {
    if (userState?.user) {
      loadPlaygroundConfig();
    }
  }, [userState?.user, loadPlaygroundConfig]);

  useEffect(() => {
    if (!canUseDebugPanel) {
      setShowDebugPanel(false);
    }
  }, [canUseDebugPanel]);

  useEffect(() => {
    if (!canUseCustomRequest) {
      setCustomRequestMode(false);
      setCustomRequestBody('');
    }
  }, [canUseCustomRequest]);

  const {
    editingMessageId,
    editValue,
    setEditValue,
    handleMessageEdit,
    handleEditSave,
    handleEditCancel,
  } = useMessageEdit(
    setMessage,
    inputs,
    parameterEnabled,
    systemPrompt,
    sendRequest,
    saveMessagesImmediately,
  );

  useSyncMessageAndCustomBody(
    customRequestMode,
    customRequestBody,
    message,
    inputs,
    setCustomRequestBody,
    setMessage,
    debouncedSaveConfig,
  );

  const constructPreviewPayload = useCallback(() => {
    try {
      if (canUseCustomRequest && customRequestMode && customRequestBody.trim()) {
        try {
          return JSON.parse(customRequestBody);
        } catch {
          // fallback to standard preview
        }
      }
      let nextMessages = [...message];
      if (!nextMessages.every((item) => item.role !== MESSAGE_ROLES.USER)) {
        for (let i = nextMessages.length - 1; i >= 0; i -= 1) {
          if (nextMessages[i].role === MESSAGE_ROLES.USER) {
            if (inputs.imageEnabled && inputs.imageUrls) {
              const validImageUrls = inputs.imageUrls.filter((url) => url.trim() !== '');
              if (validImageUrls.length > 0) {
                const textContent = getTextContent(nextMessages[i]) || '示例消息';
                const content = buildMessageContent(textContent, validImageUrls, true);
                nextMessages[i] = { ...nextMessages[i], content };
              }
            }
            break
          }
        }
      }
      return buildApiPayload(nextMessages, systemPrompt, inputs, parameterEnabled);
    } catch (error) {
      console.error('构造预览请求体失败:', error);
      return null;
    }
  }, [
    canUseCustomRequest,
    customRequestBody,
    customRequestMode,
    inputs,
    message,
    parameterEnabled,
    systemPrompt,
  ]);

  useEffect(() => {
    const timer = setTimeout(() => {
      const preview = constructPreviewPayload();
      setPreviewPayload(preview);
      setDebugData((prev) => ({
        ...(prev || {}),
        previewRequest: preview ? JSON.stringify(preview, null, 2) : null,
        previewTimestamp: preview ? new Date().toISOString() : null,
      }));
    }, 300);
    return () => clearTimeout(timer);
  }, [constructPreviewPayload]);

  const onMessageSend = useCallback(
    (content) => {
      const loadingMessage = createLoadingAssistantMessage();
      const userMessage = createMessage(
        MESSAGE_ROLES.USER,
        buildMessageContent(
          content,
          inputs.imageUrls.filter((url) => url.trim() !== ''),
          inputs.imageEnabled,
        ),
      );

      if (canUseCustomRequest && customRequestMode && customRequestBody) {
        try {
          const customPayload = JSON.parse(customRequestBody);
          setMessage((prev) => {
            const next = [...prev, userMessage, loadingMessage];
            sendRequest(customPayload, customPayload.stream !== false);
            setTimeout(() => saveMessagesImmediately(next), 0);
            return next;
          });
          return;
        } catch (error) {
          console.error('自定义请求体JSON解析失败:', error);
          Toast.error(t('JSON格式错误'));
          return;
        }
      }

      setMessage((prevMessage) => {
        const newMessages = [...prevMessage, userMessage];
        const payload = buildApiPayload(
          newMessages,
          systemPrompt,
          inputs,
          parameterEnabled,
        );
        sendRequest(payload, inputs.stream);

        if (inputs.imageEnabled) {
          setTimeout(() => {
            handleInputChange('imageEnabled', false);
          }, 100);
        }

        const messagesWithLoading = [...newMessages, loadingMessage];
        setTimeout(() => saveMessagesImmediately(messagesWithLoading), 0);
        return messagesWithLoading;
      });
    },
    [
      canUseCustomRequest,
      customRequestBody,
      customRequestMode,
      handleInputChange,
      inputs,
      parameterEnabled,
      saveMessagesImmediately,
      sendRequest,
      setMessage,
      systemPrompt,
      t,
    ],
  );

  const messageActions = useMessageActions(
    message,
    setMessage,
    onMessageSend,
    saveMessagesImmediately,
  );

  const roleInfo = useMemo(
    () => ({
      user: {
        name: userState?.user?.username || 'User',
        avatar: generateAvatarDataUrl(userState?.user?.username),
      },
      assistant: {
        name: 'Assistant',
        avatar: getLogo(),
      },
      system: {
        name: 'System',
        avatar: getLogo(),
      },
    }),
    [userState?.user?.username],
  );

  const toggleReasoningExpansion = useCallback(
    (messageId) => {
      setMessage((prevMessages) =>
        prevMessages.map((item) =>
          item.id === messageId && item.role === MESSAGE_ROLES.ASSISTANT
            ? { ...item, isReasoningExpanded: !item.isReasoningExpanded }
            : item,
        ),
      );
    },
    [setMessage],
  );

  const renderCustomChatContent = useCallback(
    ({ message: currentMessage, className }) => {
      const isCurrentlyEditing = editingMessageId === currentMessage.id;
      return (
        <OptimizedMessageContent
          message={currentMessage}
          className={className}
          styleState={styleState}
          onToggleReasoningExpansion={toggleReasoningExpansion}
          isEditing={isCurrentlyEditing}
          onEditSave={handleEditSave}
          onEditCancel={handleEditCancel}
          editValue={editValue}
          onEditValueChange={setEditValue}
        />
      );
    },
    [
      editingMessageId,
      editValue,
      handleEditCancel,
      handleEditSave,
      setEditValue,
      styleState,
      toggleReasoningExpansion,
    ],
  );

  const renderChatBoxAction = useCallback(
    (props) => {
      const { message: currentMessage } = props;
      const isAnyMessageGenerating = message.some(
        (item) => item.status === 'loading' || item.status === 'incomplete',
      );
      const isCurrentlyEditing = editingMessageId === currentMessage.id;

      return (
        <OptimizedMessageActions
          message={currentMessage}
          styleState={styleState}
          onMessageReset={messageActions.handleMessageReset}
          onMessageCopy={messageActions.handleMessageCopy}
          onMessageDelete={messageActions.handleMessageDelete}
          onRoleToggle={messageActions.handleRoleToggle}
          onMessageEdit={handleMessageEdit}
          isAnyMessageGenerating={isAnyMessageGenerating}
          isEditing={isCurrentlyEditing}
        />
      );
    },
    [editingMessageId, handleMessageEdit, message, messageActions, styleState],
  );

  useEffect(() => {
    if (searchParams.get('expired')) {
      Toast.warning(t('登录过期，请重新登录'));
    }
  }, [searchParams, t]);

  useEffect(() => {
    debouncedSaveConfig();
  }, [inputs, parameterEnabled, systemPrompt, debouncedSaveConfig]);

  const handleClearMessages = useCallback(() => {
    setMessage([]);
    setTimeout(() => saveMessagesImmediately([]), 0);
  }, [setMessage, saveMessagesImmediately]);

  const handlePasteImage = useCallback(
    (base64Data) => {
      if (!inputs.imageEnabled) {
        return;
      }
      handleInputChange('imageUrls', [...(inputs.imageUrls || []), base64Data]);
    },
    [inputs.imageEnabled, inputs.imageUrls, handleInputChange],
  );

  const playgroundContextValue = {
    onPasteImage: handlePasteImage,
    imageUrls: inputs.imageUrls || [],
    imageEnabled: inputs.imageEnabled || false,
  };

  const adminControls = isAdminUser
    ? {
        enabled: true,
        onSaveDefaults: saveScopedDefaults,
        debugVisibility: featureFlags.debugVisibility,
        customRequestVisibility: featureFlags.customRequestVisibility,
        onSaveVisibility: saveVisibility,
      }
    : null;

  return (
    <PlaygroundProvider value={playgroundContextValue}>
      <div className='h-full'>
        <Layout className='h-full bg-transparent flex flex-col md:flex-row'>
          {(showSettings || !isMobile) && (
            <Layout.Sider
              className={`bg-transparent border-r-0 flex-shrink-0 overflow-auto mt-[60px] ${
                isMobile
                  ? 'fixed top-0 left-0 right-0 bottom-0 z-[1000] w-full h-auto bg-white shadow-lg'
                  : 'relative z-[1] w-80 h-[calc(100vh-66px)]'
              }`}
              width={isMobile ? '100%' : 320}
            >
              <OptimizedSettingsPanel
                inputs={inputs}
                parameterEnabled={parameterEnabled}
                systemPrompt={systemPrompt}
                models={models}
                groups={groups}
                styleState={styleState}
                showSettings={showSettings}
                onInputChange={handleInputChange}
                onParameterToggle={handleParameterToggle}
                onCloseSettings={() => setShowSettings(false)}
                onConfigImport={handleConfigImport}
                onConfigReset={handleConfigReset}
                adminControls={adminControls}
                messages={message}
                canUseCustomRequest={canUseCustomRequest}
                customRequestMode={customRequestMode}
                customRequestBody={customRequestBody}
                onCustomRequestModeChange={setCustomRequestMode}
                onCustomRequestBodyChange={setCustomRequestBody}
                previewPayload={previewPayload}
                applyToRealAPI={applyToRealAPI}
                onApplyToRealAPIChange={saveApplyToRealAPI}
                onSavePersonalDefaults={savePersonalDefaults}
                effectHint={
                  applyToRealAPI
                    ? t('这些配置会直接作用于 Playground 请求；未显式传参的真实 API 请求也会继承这 5 个默认参数。')
                    : t('这些配置会直接作用于 Playground 发出的真实 API 请求。')
                }
              />
            </Layout.Sider>
          )}

          <Layout.Content className='relative flex-1 overflow-hidden'>
            <div className='overflow-hidden flex flex-col lg:flex-row h-[calc(100vh-66px)] mt-[60px]'>
              <div className='flex-1 flex flex-col'>
                <ChatArea
                  chatRef={chatRef}
                  message={message}
                  inputs={inputs}
                  styleState={styleState}
                  roleInfo={roleInfo}
                  showDebugPanel={showDebugPanel}
                  canUseDebugPanel={canUseDebugPanel}
                  onMessageSend={onMessageSend}
                  onMessageCopy={messageActions.handleMessageCopy}
                  onMessageReset={messageActions.handleMessageReset}
                  onMessageDelete={messageActions.handleMessageDelete}
                  onStopGenerator={onStopGenerator}
                  onClearMessages={handleClearMessages}
                  onToggleDebugPanel={() => setShowDebugPanel((prev) => !prev)}
                  renderCustomChatContent={renderCustomChatContent}
                  renderChatBoxAction={renderChatBoxAction}
                />
              </div>

              {showDebugPanel && canUseDebugPanel && !isMobile && (
                <div className='w-96 flex-shrink-0 h-full'>
                  <OptimizedDebugPanel
                    debugData={debugData}
                    activeDebugTab={activeDebugTab}
                    onActiveDebugTabChange={setActiveDebugTab}
                    styleState={styleState}
                    customRequestMode={customRequestMode}
                  />
                </div>
              )}
            </div>

            {showDebugPanel && canUseDebugPanel && isMobile && (
              <div className='fixed top-0 left-0 right-0 bottom-0 z-[1000] bg-white overflow-auto shadow-lg'>
                <OptimizedDebugPanel
                  debugData={debugData}
                  activeDebugTab={activeDebugTab}
                  onActiveDebugTabChange={setActiveDebugTab}
                  styleState={styleState}
                  onCloseDebugPanel={() => setShowDebugPanel(false)}
                  customRequestMode={customRequestMode}
                />
              </div>
            )}

            <FloatingButtons
              styleState={styleState}
              showSettings={showSettings}
              showDebugPanel={showDebugPanel}
              canUseDebugPanel={canUseDebugPanel}
              onToggleSettings={() => setShowSettings(!showSettings)}
              onToggleDebugPanel={() => setShowDebugPanel((prev) => !prev)}
            />
          </Layout.Content>
        </Layout>
      </div>
    </PlaygroundProvider>
  );
};

export default Playground;
