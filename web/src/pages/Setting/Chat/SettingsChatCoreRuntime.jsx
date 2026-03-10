import React, { useEffect, useMemo, useState } from 'react';
import {
  Banner,
  Button,
  Col,
  Form,
  Input,
  Row,
  Select,
  Spin,
  Switch,
  Table,
  TextArea,
  Typography,
} from '@douyinfe/semi-ui';
import { API, showError, showSuccess, showWarning } from '../../../helpers';

const { Text } = Typography;

const EMPTY_SETTINGS = {
  routingStrategy: 'round-robin',
  requestRetry: 0,
  maxRetryInterval: 5,
  upstreamMode: 'auto',
  codexAppServerUrl: 'ws://127.0.0.1:8787',
  serviceTier: '',
  manageCodexAppServer: true,
  autoStartCodexAppServer: true,
  reasoningEffort: 'minimal',
  reasoningSummary: 'auto',
  reasoningCompat: 'think-tags',
  exposeReasoningModels: false,
  enableWebSearch: false,
  verbose: false,
  verboseObfuscation: false,
  httpProxy: '',
  httpsProxy: '',
  allProxy: '',
  noProxy: '',
  uploadReplaceDefault: false,
};

const cardStyle = {
  border: '1px solid var(--semi-color-border)',
  borderRadius: 12,
  padding: 16,
  height: '100%',
  background: 'var(--semi-color-bg-1)',
};

const selectOptions = (items) =>
  items.map((item) =>
    typeof item === 'string'
      ? { label: item, value: item }
      : item,
  );

const safeText = (value, fallback = '-') => {
  if (value === null || value === undefined) {
    return fallback;
  }
  const text = String(value).trim();
  return text || fallback;
};

export default function SettingsChatCoreRuntime() {
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [settings, setSettings] = useState(EMPTY_SETTINGS);
  const [health, setHealth] = useState(null);
  const [accounts, setAccounts] = useState([]);
  const [models, setModels] = useState([]);
  const [configText, setConfigText] = useState('');
  const [settingsPath, setSettingsPath] = useState('');
  const [uploadFileList, setUploadFileList] = useState([]);

  const accountColumns = useMemo(
    () => [
      {
        title: '账号标签',
        dataIndex: 'label',
        render: (_, record) => safeText(record.label),
      },
      {
        title: '来源',
        dataIndex: 'source',
        render: (_, record) => safeText(record.source),
      },
      {
        title: '账号 ID',
        dataIndex: 'account_id',
        render: (_, record) => safeText(record.account_id),
      },
      {
        title: '状态',
        dataIndex: 'last_status',
        render: (_, record) => safeText(record.last_status || record.error),
      },
    ],
    [],
  );

  const getErrorMessage = (error, fallback) =>
    error?.response?.data?.message ||
    error?.response?.data?.error ||
    error?.message ||
    fallback;

  const fetchRuntimeState = async () => {
    setLoading(true);
    try {
      const [healthRes, settingsRes, accountsRes, modelsRes, configRes] =
        await Promise.all([
          API.get('/api/chatcore/admin/health', { skipErrorHandler: true }),
          API.get('/api/chatcore/admin/settings', { skipErrorHandler: true }),
          API.get('/api/chatcore/admin/accounts', { skipErrorHandler: true }),
          API.get('/api/chatcore/admin/models', { skipErrorHandler: true }),
          API.get('/api/chatcore/admin/config', { skipErrorHandler: true }),
        ]);

      setHealth(healthRes.data || null);
      setSettings({
        ...EMPTY_SETTINGS,
        ...(settingsRes.data?.settings || {}),
      });
      setSettingsPath(settingsRes.data?.settingsPath || '');
      setAccounts(
        Array.isArray(accountsRes.data?.accounts) ? accountsRes.data.accounts : [],
      );
      setModels(Array.isArray(modelsRes.data?.ids) ? modelsRes.data.ids : []);
      setConfigText(configRes.data?.activeConfig || configRes.data?.localConfig || '');
    } catch (error) {
      showError(getErrorMessage(error, '读取内嵌 chat 状态失败'));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchRuntimeState();
  }, []);

  const handleSettingChange = (key, value) => {
    setSettings((prev) => ({
      ...prev,
      [key]: value,
    }));
  };

  const handleSaveSettings = async () => {
    setSaving(true);
    try {
      const res = await API.post('/api/chatcore/admin/settings', settings, {
        skipErrorHandler: true,
      });
      setSettings({
        ...EMPTY_SETTINGS,
        ...(res.data?.settings || settings),
      });
      setSettingsPath(res.data?.settingsPath || settingsPath);
      showSuccess('ChatCore 参数已保存');
      await fetchRuntimeState();
    } catch (error) {
      showError(getErrorMessage(error, 'ChatCore 参数保存失败'));
    } finally {
      setSaving(false);
    }
  };

  const handleUploadAuths = async () => {
    if (!uploadFileList.length) {
      showWarning('请先选择一个或多个 auth.json');
      return;
    }

    const formData = new FormData();
    formData.append('replace', settings.uploadReplaceDefault ? '1' : '0');

    uploadFileList.forEach((item, index) => {
      const fileObj = item.fileInstance;
      if (fileObj) {
        formData.append(
          'files',
          fileObj,
          fileObj.name || item.name || `auth-${index + 1}.json`,
        );
      }
    });

    setUploading(true);
    try {
      const res = await API.post('/api/chatcore/admin/upload_auths', formData, {
        skipErrorHandler: true,
      });
      setUploadFileList([]);
      showSuccess(`已上传 ${res.data?.uploaded || 0} 个 auth.json`);
      await fetchRuntimeState();
    } catch (error) {
      showError(getErrorMessage(error, 'auth.json 上传失败'));
    } finally {
      setUploading(false);
    }
  };

  const metric = (title, value, subtext) => (
    <div style={cardStyle}>
      <Text strong>{title}</Text>
      <div style={{ fontSize: 22, fontWeight: 700, marginTop: 8 }}>
        {safeText(value)}
      </div>
      {subtext ? (
        <Text type='tertiary' size='small'>
          {subtext}
        </Text>
      ) : null}
    </div>
  );

  const controlBlock = (label, node) => (
    <div style={{ marginBottom: 12 }}>
      <Text>{label}</Text>
      <div style={{ marginTop: 8 }}>{node}</div>
    </div>
  );

  return (
    <Spin spinning={loading}>
      <Form>
        <Form.Section text='ChatCore 单服务管理'>
          <Banner
            type='info'
            closeIcon={null}
            description='这里管理容器内嵌 chat 的账号池、上游模式和运行参数。外部客户端继续只连接 II.fy，对内由本服务转到完整 chat 内核。'
            style={{ marginBottom: 16 }}
          />

          <Row gutter={16} style={{ marginBottom: 16 }}>
            <Col xs={24} sm={12} md={6}>
              {metric(
                '服务状态',
                health?.service?.status || 'unknown',
                health?.service?.raw || '等待检测',
              )}
            </Col>
            <Col xs={24} sm={12} md={6}>
              {metric('账号数量', health?.accounts?.count || 0, '当前 auth 账号池')}
            </Col>
            <Col xs={24} sm={12} md={6}>
              {metric('模型数量', health?.models?.count || 0, '完整 chat 暴露模型')}
            </Col>
            <Col xs={24} sm={12} md={6}>
              {metric('设置文件', settingsPath || '-', 'dashboard settings path')}
            </Col>
          </Row>

          <Row gutter={16}>
            <Col xs={24} lg={16}>
              <div style={cardStyle}>
                <Text strong>运行参数</Text>
                <Row gutter={16} style={{ marginTop: 12 }}>
                  <Col xs={24} sm={12}>
                    {controlBlock(
                      '轮询策略',
                      <Select
                        value={settings.routingStrategy || 'round-robin'}
                        optionList={selectOptions(['round-robin', 'random', 'first'])}
                        onChange={(value) => handleSettingChange('routingStrategy', value)}
                      />,
                    )}
                  </Col>
                  <Col xs={24} sm={12}>
                    {controlBlock(
                      '请求重试',
                      <Input
                        value={String(settings.requestRetry ?? 0)}
                        onChange={(value) => handleSettingChange('requestRetry', value)}
                      />,
                    )}
                  </Col>
                  <Col xs={24} sm={12}>
                    {controlBlock(
                      '最大重试间隔（秒）',
                      <Input
                        value={String(settings.maxRetryInterval ?? 5)}
                        onChange={(value) => handleSettingChange('maxRetryInterval', value)}
                      />,
                    )}
                  </Col>
                  <Col xs={24} sm={12}>
                    {controlBlock(
                      '上游模式',
                      <Select
                        value={settings.upstreamMode || 'auto'}
                        optionList={selectOptions([
                          { label: 'auto（按模型家族自动分流）', value: 'auto' },
                          { label: 'chatgpt-backend', value: 'chatgpt-backend' },
                          { label: 'codex-app-server', value: 'codex-app-server' },
                        ])}
                        onChange={(value) => handleSettingChange('upstreamMode', value)}
                      />,
                    )}
                  </Col>
                  <Col xs={24} sm={12}>
                    {controlBlock(
                      'Codex App Server URL',
                      <Input
                        value={settings.codexAppServerUrl || 'ws://127.0.0.1:8787'}
                        onChange={(value) => handleSettingChange('codexAppServerUrl', value)}
                      />,
                    )}
                  </Col>
                  <Col xs={24} sm={12}>
                    {controlBlock(
                      'Service Tier',
                      <Select
                        value={settings.serviceTier ?? ''}
                        optionList={selectOptions([
                          { label: '默认 / 不透传', value: '' },
                          { label: 'fast', value: 'fast' },
                          { label: 'flex', value: 'flex' },
                          { label: 'priority', value: 'priority' },
                        ])}
                        onChange={(value) => handleSettingChange('serviceTier', value)}
                      />,
                    )}
                  </Col>
                  <Col xs={24} sm={12}>
                    {controlBlock(
                      'Reasoning Effort',
                      <Select
                        value={settings.reasoningEffort || 'minimal'}
                        optionList={selectOptions(['minimal', 'low', 'medium', 'high', 'xhigh'])}
                        onChange={(value) => handleSettingChange('reasoningEffort', value)}
                      />,
                    )}
                  </Col>
                  <Col xs={24} sm={12}>
                    {controlBlock(
                      'Reasoning Summary',
                      <Select
                        value={settings.reasoningSummary || 'auto'}
                        optionList={selectOptions(['auto', 'concise', 'detailed', 'none'])}
                        onChange={(value) => handleSettingChange('reasoningSummary', value)}
                      />,
                    )}
                  </Col>
                  <Col xs={24} sm={12}>
                    {controlBlock(
                      'Reasoning Compat',
                      <Select
                        value={settings.reasoningCompat || 'think-tags'}
                        optionList={selectOptions(['legacy', 'o3', 'think-tags', 'current'])}
                        onChange={(value) => handleSettingChange('reasoningCompat', value)}
                      />,
                    )}
                  </Col>
                  <Col xs={24} sm={12}>
                    {controlBlock(
                      'HTTP_PROXY',
                      <Input
                        value={settings.httpProxy || ''}
                        onChange={(value) => handleSettingChange('httpProxy', value)}
                      />,
                    )}
                  </Col>
                  <Col xs={24} sm={12}>
                    {controlBlock(
                      'HTTPS_PROXY',
                      <Input
                        value={settings.httpsProxy || ''}
                        onChange={(value) => handleSettingChange('httpsProxy', value)}
                      />,
                    )}
                  </Col>
                  <Col xs={24} sm={12}>
                    {controlBlock(
                      'ALL_PROXY',
                      <Input
                        value={settings.allProxy || ''}
                        onChange={(value) => handleSettingChange('allProxy', value)}
                      />,
                    )}
                  </Col>
                  <Col xs={24} sm={12}>
                    {controlBlock(
                      'NO_PROXY',
                      <Input
                        value={settings.noProxy || ''}
                        onChange={(value) => handleSettingChange('noProxy', value)}
                      />,
                    )}
                  </Col>
                </Row>

                <Row gutter={16} style={{ marginTop: 8 }}>
                  <Col xs={24} sm={12} md={8}>
                    <div style={{ marginBottom: 12 }}>
                      <Text>暴露推理模型</Text>
                      <div>
                        <Switch
                          checked={Boolean(settings.exposeReasoningModels)}
                          onChange={(value) =>
                            handleSettingChange('exposeReasoningModels', value)
                          }
                        />
                      </div>
                    </div>
                  </Col>
                  <Col xs={24} sm={12} md={8}>
                    <div style={{ marginBottom: 12 }}>
                      <Text>默认开启 Web Search</Text>
                      <div>
                        <Switch
                          checked={Boolean(settings.enableWebSearch)}
                          onChange={(value) =>
                            handleSettingChange('enableWebSearch', value)
                          }
                        />
                      </div>
                    </div>
                  </Col>
                  <Col xs={24} sm={12} md={8}>
                    <div style={{ marginBottom: 12 }}>
                      <Text>Verbose</Text>
                      <div>
                        <Switch
                          checked={Boolean(settings.verbose)}
                          onChange={(value) => handleSettingChange('verbose', value)}
                        />
                      </div>
                    </div>
                  </Col>
                  <Col xs={24} sm={12} md={8}>
                    <div style={{ marginBottom: 12 }}>
                      <Text>Verbose Obfuscation</Text>
                      <div>
                        <Switch
                          checked={Boolean(settings.verboseObfuscation)}
                          onChange={(value) =>
                            handleSettingChange('verboseObfuscation', value)
                          }
                        />
                      </div>
                    </div>
                  </Col>
                  <Col xs={24} sm={12} md={8}>
                    <div style={{ marginBottom: 12 }}>
                      <Text>托管 Codex App Server</Text>
                      <div>
                        <Switch
                          checked={Boolean(settings.manageCodexAppServer)}
                          onChange={(value) =>
                            handleSettingChange('manageCodexAppServer', value)
                          }
                        />
                      </div>
                    </div>
                  </Col>
                  <Col xs={24} sm={12} md={8}>
                    <div style={{ marginBottom: 12 }}>
                      <Text>自动启动 Codex App Server</Text>
                      <div>
                        <Switch
                          checked={Boolean(settings.autoStartCodexAppServer)}
                          onChange={(value) =>
                            handleSettingChange('autoStartCodexAppServer', value)
                          }
                        />
                      </div>
                    </div>
                  </Col>
                  <Col xs={24} sm={12} md={8}>
                    <div style={{ marginBottom: 12 }}>
                      <Text>上传时替换现有账号池</Text>
                      <div>
                        <Switch
                          checked={Boolean(settings.uploadReplaceDefault)}
                          onChange={(value) =>
                            handleSettingChange('uploadReplaceDefault', value)
                          }
                        />
                      </div>
                    </div>
                  </Col>
                </Row>

                <Button type='primary' onClick={handleSaveSettings} loading={saving}>
                  保存 chat 参数
                </Button>
              </div>
            </Col>

            <Col xs={24} lg={8}>
              <div style={cardStyle}>
                <Text strong>上传 auth.json</Text>
                <Form.Upload
                  field='chatmock_auth_files'
                  accept='.json'
                  draggable
                  multiple
                  uploadTrigger='custom'
                  beforeUpload={() => false}
                  fileList={uploadFileList}
                  onChange={({ fileList }) => setUploadFileList(fileList || [])}
                  dragMainText='点击或拖拽 auth.json 到这里'
                  dragSubText='支持一次上传多个账号文件'
                  style={{ marginTop: 12 }}
                />
                <Text type='tertiary' size='small'>
                  当前模式：
                  {settings.uploadReplaceDefault ? '替换现有账号池' : '追加到现有账号池'}
                </Text>
                <div style={{ marginTop: 12 }}>
                  <Button type='primary' onClick={handleUploadAuths} loading={uploading}>
                    上传 auth.json
                  </Button>
                </div>
              </div>
            </Col>
          </Row>

          <div style={{ ...cardStyle, marginTop: 16 }}>
            <Text strong>账号列表</Text>
            <Table
              style={{ marginTop: 12 }}
              rowKey={(record, index) => `${record.label || 'acc'}-${index}`}
              dataSource={accounts}
              columns={accountColumns}
              pagination={false}
              empty='暂无账号'
            />
          </div>

          <div style={{ ...cardStyle, marginTop: 16 }}>
            <Text strong>当前暴露模型</Text>
            <TextArea
              autosize={{ minRows: 4, maxRows: 10 }}
              value={models.join(', ')}
              readOnly
              style={{ marginTop: 12 }}
            />
          </div>

          <div style={{ ...cardStyle, marginTop: 16 }}>
            <Text strong>当前生效配置</Text>
            <TextArea
              autosize={{ minRows: 10, maxRows: 18 }}
              value={configText}
              readOnly
              style={{ marginTop: 12 }}
            />
          </div>
        </Form.Section>
      </Form>
    </Spin>
  );
}
