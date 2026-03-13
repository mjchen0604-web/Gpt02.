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

import { useState, useCallback, useRef, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import {
  getDefaultMessages,
  DEFAULT_CONFIG,
  MESSAGE_STATUS,
} from '../../constants/playground.constants';
import {
  loadConfig,
  loadRawConfig,
  loadMessages,
  saveConfig,
  saveMessages,
} from '../../components/playground/configStorage';
import { processIncompleteThinkTags } from '../../helpers';

export const usePlaygroundState = () => {
  const { t } = useTranslation();

  const [savedConfigRaw] = useState(() => loadRawConfig() || {});
  const [savedConfig] = useState(() => loadConfig());
  const [initialMessages] = useState(() => {
    const loaded = loadMessages();
    if (
      loaded &&
      loaded.length === 2 &&
      loaded[0].id === '2' &&
      loaded[1].id === '3'
    ) {
      const hasOldChinese =
        loaded[0].content === '你好' ||
        loaded[1].content === '你好，请问有什么可以帮助您的吗？' ||
        loaded[1].content === '你好！很高兴见到你。有什么我可以帮助你的吗？';
      if (hasOldChinese) {
        localStorage.removeItem('playground_messages');
        return null;
      }
    }
    return loaded;
  });

  const [inputs, setInputs] = useState(savedConfig.inputs || DEFAULT_CONFIG.inputs);
  const [parameterEnabled, setParameterEnabled] = useState(
    savedConfig.parameterEnabled || DEFAULT_CONFIG.parameterEnabled,
  );
  const [showSettings, setShowSettings] = useState(false);
  const [models, setModels] = useState([]);
  const [groups, setGroups] = useState([]);
  const [message, setMessage] = useState(() => initialMessages || getDefaultMessages(t));
  const [editingMessageId, setEditingMessageId] = useState(null);
  const [editValue, setEditValue] = useState('');

  const sseSourceRef = useRef(null);
  const chatRef = useRef(null);
  const saveConfigTimeoutRef = useRef(null);

  useEffect(() => {
    if (!initialMessages) {
      setMessage(getDefaultMessages(t));
    }
  }, [t, initialMessages]);

  const handleInputChange = useCallback((name, value) => {
    setInputs((prev) => ({ ...prev, [name]: value }));
  }, []);

  const handleParameterToggle = useCallback((paramName) => {
    setParameterEnabled((prev) => ({
      ...prev,
      [paramName]: !prev[paramName],
    }));
  }, []);

  const saveMessagesImmediately = useCallback(
    (messagesToSave) => {
      saveMessages(messagesToSave || message);
    },
    [message],
  );

  const debouncedSaveConfig = useCallback(() => {
    if (saveConfigTimeoutRef.current) {
      clearTimeout(saveConfigTimeoutRef.current);
    }

    saveConfigTimeoutRef.current = setTimeout(() => {
      saveConfig({
        inputs,
        parameterEnabled,
      });
    }, 1000);
  }, [inputs, parameterEnabled]);

  const handleConfigImport = useCallback((importedConfig) => {
    if (importedConfig.inputs) {
      setInputs((prev) => ({ ...prev, ...importedConfig.inputs }));
    }
    if (importedConfig.parameterEnabled) {
      setParameterEnabled((prev) => ({
        ...prev,
        ...importedConfig.parameterEnabled,
      }));
    }
    if (importedConfig.messages && Array.isArray(importedConfig.messages)) {
      setMessage(importedConfig.messages);
    }
  }, []);

  const handleConfigReset = useCallback(
    (options = {}) => {
      const { resetMessages = false } = options;
      setInputs(DEFAULT_CONFIG.inputs);
      setParameterEnabled(DEFAULT_CONFIG.parameterEnabled);

      if (resetMessages) {
        setMessage([]);
        setTimeout(() => {
          setMessage(getDefaultMessages(t));
        }, 0);
      }
    },
    [t],
  );

  useEffect(() => {
    return () => {
      if (saveConfigTimeoutRef.current) {
        clearTimeout(saveConfigTimeoutRef.current);
      }
    };
  }, []);

  const applyRemoteDefaults = useCallback(
    (remoteDefaults = {}, isAdminUser = false) => {
      const globalDefaults =
        remoteDefaults && typeof remoteDefaults.globalDefaults === 'object'
          ? remoteDefaults.globalDefaults
          : {};
      const adminDefaults =
        isAdminUser &&
        remoteDefaults &&
        typeof remoteDefaults.adminDefaults === 'object'
          ? remoteDefaults.adminDefaults
          : {};
      const personalDefaults =
        remoteDefaults &&
        typeof remoteDefaults.personalDefaults === 'object'
          ? remoteDefaults.personalDefaults
          : {};

      setInputs({
        ...DEFAULT_CONFIG.inputs,
        ...(globalDefaults.inputs || {}),
        ...(adminDefaults.inputs || {}),
        ...(personalDefaults.inputs || {}),
        ...(savedConfigRaw.inputs || {}),
      });
      setParameterEnabled({
        ...DEFAULT_CONFIG.parameterEnabled,
        ...(globalDefaults.parameterEnabled || {}),
        ...(adminDefaults.parameterEnabled || {}),
        ...(personalDefaults.parameterEnabled || {}),
        ...(savedConfigRaw.parameterEnabled || {}),
      });
    },
    [savedConfigRaw],
  );

  useEffect(() => {
    if (!Array.isArray(message) || message.length === 0) {
      return;
    }

    const lastMsg = message[message.length - 1];
    if (
      lastMsg.status === MESSAGE_STATUS.LOADING ||
      lastMsg.status === MESSAGE_STATUS.INCOMPLETE
    ) {
      const processed = processIncompleteThinkTags(
        lastMsg.content || '',
        lastMsg.reasoningContent || '',
      );
      const fixedLastMsg = {
        ...lastMsg,
        status: MESSAGE_STATUS.COMPLETE,
        content: processed.content,
        reasoningContent: processed.reasoningContent || null,
        isThinkingComplete: true,
      };
      const updatedMessages = [...message.slice(0, -1), fixedLastMsg];
      setMessage(updatedMessages);
      setTimeout(() => saveMessagesImmediately(updatedMessages), 0);
    }
  }, []);

  return {
    inputs,
    parameterEnabled,
    showSettings,
    models,
    groups,
    message,
    editingMessageId,
    editValue,
    sseSourceRef,
    chatRef,
    saveConfigTimeoutRef,
    setInputs,
    setParameterEnabled,
    setShowSettings,
    setModels,
    setGroups,
    setMessage,
    setEditingMessageId,
    setEditValue,
    handleInputChange,
    handleParameterToggle,
    debouncedSaveConfig,
    saveMessagesImmediately,
    handleConfigImport,
    handleConfigReset,
    applyRemoteDefaults,
  };
};
