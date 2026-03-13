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

import React from 'react';
import {
  Button,
  Card,
  Select,
  Switch,
  TextArea,
  Typography,
} from '@douyinfe/semi-ui';
import {
  Bug,
  Code,
  FileText,
  Settings,
  Sparkles,
  ToggleLeft,
  Users,
  X,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { renderGroupOption, selectFilter } from '../../helpers';
import ConfigManager from './ConfigManager';
import CustomRequestEditor from './CustomRequestEditor';
import ImageUrlInput from './ImageUrlInput';
import ParameterControl from './ParameterControl';

const VISIBILITY_OPTIONS = [
  { label: '关闭', value: 'off' },
  { label: '仅管理员', value: 'admin' },
  { label: '全局开放', value: 'global' },
];

const SettingsPanel = ({
  inputs,
  parameterEnabled,
  systemPrompt,
  models,
  groups,
  styleState,
  onInputChange,
  onParameterToggle,
  onCloseSettings,
  onConfigImport,
  onConfigReset,
  adminControls,
  messages,
  canUseCustomRequest,
  customRequestMode,
  customRequestBody,
  onCustomRequestModeChange,
  onCustomRequestBodyChange,
  previewPayload,
  effectHint,
  applyToRealAPI,
  onApplyToRealAPIChange,
  onSavePersonalDefaults,
}) => {
  const { t } = useTranslation();

  const currentConfig = {
    inputs,
    parameterEnabled,
    systemPrompt,
  };

  return (
    <Card
      className='h-full flex flex-col'
      bordered={false}
      bodyStyle={{
        padding: styleState.isMobile ? '16px' : '24px',
        height: '100%',
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      <div className='flex items-center justify-between mb-6 flex-shrink-0'>
        <div className='flex items-center'>
          <div className='w-10 h-10 rounded-full bg-gradient-to-r from-purple-500 to-pink-500 flex items-center justify-center mr-3'>
            <Settings size={20} className='text-white' />
          </div>
          <Typography.Title heading={5} className='mb-0'>
            {t('模型配置')}
          </Typography.Title>
        </div>

        {styleState.isMobile && onCloseSettings && (
          <Button
            icon={<X size={16} />}
            onClick={onCloseSettings}
            theme='borderless'
            type='tertiary'
            size='small'
            className='!rounded-lg'
          />
        )}
      </div>

      {styleState.isMobile && (
        <div className='mb-4 flex-shrink-0'>
          <ConfigManager
            currentConfig={currentConfig}
            onConfigImport={onConfigImport}
            onConfigReset={onConfigReset}
            styleState={{ ...styleState, isMobile: false }}
            adminControls={adminControls}
            messages={messages}
            onSavePersonalDefaults={onSavePersonalDefaults}
          />
        </div>
      )}

      <div className='space-y-6 overflow-y-auto flex-1 pr-2 model-settings-scroll'>
        <div>
          <div className='flex items-center gap-2 mb-2'>
            <Users size={16} className='text-gray-500' />
            <Typography.Text strong className='text-sm'>
              {t('分组')}
            </Typography.Text>
          </div>
          <Select
            placeholder={t('请选择分组')}
            name='group'
            required
            selection
            filter={selectFilter}
            autoClearSearchValue={false}
            onChange={(value) => onInputChange('group', value)}
            value={inputs.group}
            autoComplete='new-password'
            optionList={groups}
            renderOptionItem={renderGroupOption}
            style={{ width: '100%' }}
            dropdownStyle={{ width: '100%', maxWidth: '100%' }}
            className='!rounded-lg'
          />
        </div>

        <div>
          <div className='flex items-center gap-2 mb-2'>
            <Sparkles size={16} className='text-gray-500' />
            <Typography.Text strong className='text-sm'>
              {t('模型')}
            </Typography.Text>
          </div>
          <Select
            placeholder={t('请选择模型')}
            name='model'
            required
            selection
            filter={selectFilter}
            autoClearSearchValue={false}
            onChange={(value) => onInputChange('model', value)}
            value={inputs.model}
            autoComplete='new-password'
            optionList={models}
            style={{ width: '100%' }}
            dropdownStyle={{ width: '100%', maxWidth: '100%' }}
            className='!rounded-lg'
          />
          {effectHint && (
            <Typography.Text className='text-xs text-gray-500 mt-2 block'>
              {effectHint}
            </Typography.Text>
          )}
        </div>

        <div>
          <div className='flex items-center justify-between gap-3'>
            <div>
              <Typography.Text strong className='text-sm'>
                {t('作用到真实 API')}
              </Typography.Text>
              <Typography.Text className='text-xs text-gray-500 block mt-1'>
                {t('开启后，未显式传参的真实 API 请求也会继承这 6 个默认参数。')}
              </Typography.Text>
            </div>
            <Switch
              checked={applyToRealAPI}
              onChange={onApplyToRealAPIChange}
              checkedText={t('开')}
              uncheckedText={t('关')}
              size='small'
            />
          </div>
        </div>

        <div>
          <ImageUrlInput
            imageUrls={inputs.imageUrls}
            imageEnabled={inputs.imageEnabled}
            onImageUrlsChange={(urls) => onInputChange('imageUrls', urls)}
            onImageEnabledChange={(enabled) => onInputChange('imageEnabled', enabled)}
          />
        </div>

        <div>
          <ParameterControl
            inputs={inputs}
            parameterEnabled={parameterEnabled}
            onInputChange={onInputChange}
            onParameterToggle={onParameterToggle}
          />
        </div>

        <div>
          <div className='flex items-center gap-2 mb-2'>
            <FileText size={16} className='text-gray-500' />
            <Typography.Text strong className='text-sm'>
              {t('System Prompt')}
            </Typography.Text>
          </div>
          <TextArea
            value={systemPrompt}
            onChange={(value) => onInputChange('systemPrompt', value)}
            autosize={{ minRows: 4, maxRows: 10 }}
            placeholder={t('输入系统提示词')}
            className='!rounded-lg'
          />
        </div>

        {adminControls?.enabled && (
          <div className='space-y-4 rounded-xl border border-[var(--semi-color-border)] p-3'>
            <div className='flex items-center gap-2'>
              <Bug size={16} className='text-gray-500' />
              <Typography.Text strong className='text-sm'>
                {t('实验功能可见性')}
              </Typography.Text>
            </div>
            <div>
              <Typography.Text className='text-xs text-gray-500 mb-2 block'>
                {t('调试信息')}
              </Typography.Text>
              <Select
                value={adminControls.debugVisibility}
                optionList={VISIBILITY_OPTIONS}
                onChange={(value) => adminControls.onSaveVisibility?.('debug', value)}
                className='!rounded-lg'
              />
            </div>
            <div>
              <Typography.Text className='text-xs text-gray-500 mb-2 block'>
                {t('自定义请求体模式')}
              </Typography.Text>
              <Select
                value={adminControls.customRequestVisibility}
                optionList={VISIBILITY_OPTIONS}
                onChange={(value) =>
                  adminControls.onSaveVisibility?.('custom_request', value)
                }
                className='!rounded-lg'
              />
            </div>
          </div>
        )}

        {canUseCustomRequest && (
          <CustomRequestEditor
            customRequestMode={customRequestMode}
            customRequestBody={customRequestBody}
            onCustomRequestModeChange={onCustomRequestModeChange}
            onCustomRequestBodyChange={onCustomRequestBodyChange}
            defaultPayload={previewPayload}
          />
        )}

        <div>
          <div className='flex items-center justify-between'>
            <div className='flex items-center gap-2'>
              <ToggleLeft size={16} className='text-gray-500' />
              <Typography.Text strong className='text-sm'>
                {t('流式输出')}
              </Typography.Text>
            </div>
            <Switch
              checked={inputs.stream}
              onChange={(checked) => onInputChange('stream', checked)}
              checkedText={t('开')}
              uncheckedText={t('关')}
              size='small'
            />
          </div>
        </div>
      </div>

      {!styleState.isMobile && (
        <div className='flex-shrink-0 pt-3'>
          <ConfigManager
            currentConfig={currentConfig}
            onConfigImport={onConfigImport}
            onConfigReset={onConfigReset}
            styleState={styleState}
            adminControls={adminControls}
            messages={messages}
            onSavePersonalDefaults={onSavePersonalDefaults}
          />
        </div>
      )}
    </Card>
  );
};

export default SettingsPanel;
