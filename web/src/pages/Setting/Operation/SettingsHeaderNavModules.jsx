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

import React, { useEffect, useState, useContext } from 'react';
import {
  Button,
  Card,
  Col,
  Form,
  Row,
  Switch,
  Typography,
} from '@douyinfe/semi-ui';
import { API, showError, showSuccess } from '../../../helpers';
import { StatusContext } from '../../../context/Status';

const { Text } = Typography;

const getDefaultModules = () => ({
  home: true,
  console: true,
  pricing: {
    enabled: true,
    requireAuth: false,
  },
  about: true,
});

export default function SettingsHeaderNavModules(props) {
  const [loading, setLoading] = useState(false);
  const [statusState, statusDispatch] = useContext(StatusContext);
  const [headerNavModules, setHeaderNavModules] = useState(getDefaultModules());

  function handleHeaderNavModuleChange(moduleKey) {
    return (checked) => {
      const newModules = { ...headerNavModules };
      if (moduleKey === 'pricing') {
        newModules[moduleKey] = {
          ...newModules[moduleKey],
          enabled: checked,
        };
      } else {
        newModules[moduleKey] = checked;
      }
      setHeaderNavModules(newModules);
    };
  }

  function handlePricingAuthChange(checked) {
    setHeaderNavModules((current) => ({
      ...current,
      pricing: {
        ...current.pricing,
        requireAuth: checked,
      },
    }));
  }

  function resetHeaderNavModules() {
    setHeaderNavModules(getDefaultModules());
    showSuccess('已重置为默认配置');
  }

  async function onSubmit() {
    setLoading(true);
    try {
      const res = await API.put('/api/option/', {
        key: 'HeaderNavModules',
        value: JSON.stringify(headerNavModules),
      });
      const { success, message } = res.data;
      if (success) {
        showSuccess('保存成功');
        statusDispatch({
          type: 'set',
          payload: {
            ...statusState.status,
            HeaderNavModules: JSON.stringify(headerNavModules),
          },
        });
        if (props.refresh) {
          await props.refresh();
        }
      } else {
        showError(message);
      }
    } catch (error) {
      showError('保存失败，请重试');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (props.options && props.options.HeaderNavModules) {
      try {
        const modules = JSON.parse(props.options.HeaderNavModules);
        const defaults = getDefaultModules();
        setHeaderNavModules({
          home:
            typeof modules.home === 'boolean'
              ? modules.home
              : defaults.home,
          console:
            typeof modules.console === 'boolean'
              ? modules.console
              : defaults.console,
          pricing:
            typeof modules.pricing === 'object'
              ? {
                  enabled:
                    modules.pricing.enabled ?? defaults.pricing.enabled,
                  requireAuth:
                    modules.pricing.requireAuth ??
                    defaults.pricing.requireAuth,
                }
              : {
                  enabled:
                    typeof modules.pricing === 'boolean'
                      ? modules.pricing
                      : defaults.pricing.enabled,
                  requireAuth: defaults.pricing.requireAuth,
                },
          about:
            typeof modules.about === 'boolean'
              ? modules.about
              : defaults.about,
        });
      } catch (error) {
        setHeaderNavModules(getDefaultModules());
      }
    }
  }, [props.options]);

  const moduleConfigs = [
    {
      key: 'home',
      title: '首页',
      description: '首页只展示品牌 Logo',
    },
    {
      key: 'console',
      title: '控制台',
      description: '用户控制面板和管理入口',
    },
    {
      key: 'pricing',
      title: '模型广场',
      description: '模型价格和可用模型展示',
    },
    {
      key: 'about',
      title: '关于',
      description: '品牌和系统相关信息',
    },
  ];

  return (
    <Card>
      <Form.Section
        text='顶部栏管理'
        extraText='控制顶部栏模块显示状态，全局生效'
      >
        <Row gutter={[16, 16]} style={{ marginBottom: '24px' }}>
          {moduleConfigs.map((module) => (
            <Col key={module.key} xs={24} sm={12} md={6} lg={6} xl={6}>
              <Card
                style={{
                  borderRadius: '8px',
                  border: '1px solid var(--semi-color-border)',
                  transition: 'all 0.2s ease',
                  background: 'var(--semi-color-bg-1)',
                  minHeight: '80px',
                }}
                bodyStyle={{ padding: '16px' }}
                hoverable
              >
                <div
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    height: '100%',
                  }}
                >
                  <div style={{ flex: 1, textAlign: 'left' }}>
                    <div
                      style={{
                        fontWeight: '600',
                        fontSize: '14px',
                        color: 'var(--semi-color-text-0)',
                        marginBottom: '4px',
                      }}
                    >
                      {module.title}
                    </div>
                    <Text
                      type='secondary'
                      size='small'
                      style={{
                        fontSize: '12px',
                        color: 'var(--semi-color-text-2)',
                        lineHeight: '1.4',
                        display: 'block',
                      }}
                    >
                      {module.description}
                    </Text>
                  </div>
                  <div style={{ marginLeft: '16px' }}>
                    <Switch
                      checked={
                        module.key === 'pricing'
                          ? headerNavModules[module.key]?.enabled
                          : headerNavModules[module.key]
                      }
                      onChange={handleHeaderNavModuleChange(module.key)}
                      size='default'
                    />
                  </div>
                </div>

                {module.key === 'pricing' &&
                  headerNavModules.pricing?.enabled && (
                    <div
                      style={{
                        borderTop: '1px solid var(--semi-color-border)',
                        marginTop: '12px',
                        paddingTop: '12px',
                      }}
                    >
                      <div
                        style={{
                          display: 'flex',
                          justifyContent: 'space-between',
                          alignItems: 'center',
                        }}
                      >
                        <div style={{ flex: 1, textAlign: 'left' }}>
                          <div
                            style={{
                              fontWeight: '500',
                              fontSize: '12px',
                              color: 'var(--semi-color-text-1)',
                              marginBottom: '2px',
                            }}
                          >
                            需要登录访问
                          </div>
                          <Text
                            type='secondary'
                            size='small'
                            style={{
                              fontSize: '11px',
                              color: 'var(--semi-color-text-2)',
                              lineHeight: '1.4',
                              display: 'block',
                            }}
                          >
                            开启后未登录用户无法访问模型广场
                          </Text>
                        </div>
                        <div style={{ marginLeft: '16px' }}>
                          <Switch
                            checked={headerNavModules.pricing?.requireAuth || false}
                            onChange={handlePricingAuthChange}
                            size='default'
                          />
                        </div>
                      </div>
                    </div>
                  )}
              </Card>
            </Col>
          ))}
        </Row>

        <div
          style={{
            display: 'flex',
            gap: '12px',
            justifyContent: 'flex-start',
            alignItems: 'center',
            paddingTop: '8px',
            borderTop: '1px solid var(--semi-color-border)',
          }}
        >
          <Button
            size='default'
            type='tertiary'
            onClick={resetHeaderNavModules}
            style={{
              borderRadius: '6px',
              fontWeight: '500',
            }}
          >
            重置为默认
          </Button>
          <Button
            size='default'
            type='primary'
            onClick={onSubmit}
            loading={loading}
            style={{
              borderRadius: '6px',
              fontWeight: '500',
              minWidth: '100px',
            }}
          >
            保存设置
          </Button>
        </div>
      </Form.Section>
    </Card>
  );
}
