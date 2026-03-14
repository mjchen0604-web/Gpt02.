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

import React, { useContext, useMemo, useState } from 'react';
import { Layout, ImagePreview, Button, Space, Switch, Typography } from '@douyinfe/semi-ui';
import PricingSidebar from './PricingSidebar';
import PricingContent from './content/PricingContent';
import ModelDetailSideSheet from '../modal/ModelDetailSideSheet';
import { useModelPricingData } from '../../../../hooks/model-pricing/useModelPricingData';
import { useIsMobile } from '../../../../hooks/common/useIsMobile';
import { API, showError, showSuccess } from '../../../../helpers';
import { UserContext } from '../../../../context/User';
import { StatusContext } from '../../../../context/Status';

const { Text } = Typography;

const getDefaultHeaderNavModules = () => ({
  home: true,
  console: true,
  pricing: {
    enabled: true,
    requireAuth: false,
  },
  about: true,
});

const PricingPage = () => {
  const pricingData = useModelPricingData();
  const { Sider, Content } = Layout;
  const isMobile = useIsMobile();
  const [userState] = useContext(UserContext);
  const [statusState, statusDispatch] = useContext(StatusContext);
  const [showRatio, setShowRatio] = React.useState(false);
  const [viewMode, setViewMode] = React.useState('card');
  const [updatingMarketplaceNav, setUpdatingMarketplaceNav] = useState(false);

  const isAdmin = useMemo(() => {
    const role = userState?.user?.role;
    return typeof role === 'number' && role >= 10;
  }, [userState]);

  const headerNavModules = useMemo(() => {
    const defaults = getDefaultHeaderNavModules();
    const raw = statusState?.status?.HeaderNavModules;
    if (!raw) {
      return defaults;
    }
    try {
      const parsed = JSON.parse(raw);
      return {
        home: typeof parsed.home === 'boolean' ? parsed.home : defaults.home,
        console:
          typeof parsed.console === 'boolean'
            ? parsed.console
            : defaults.console,
        pricing:
          typeof parsed.pricing === 'object'
            ? {
                enabled:
                  parsed.pricing.enabled ?? defaults.pricing.enabled,
                requireAuth:
                  parsed.pricing.requireAuth ?? defaults.pricing.requireAuth,
              }
            : {
                enabled:
                  typeof parsed.pricing === 'boolean'
                    ? parsed.pricing
                    : defaults.pricing.enabled,
                requireAuth: defaults.pricing.requireAuth,
              },
        about: typeof parsed.about === 'boolean' ? parsed.about : defaults.about,
      };
    } catch (error) {
      return defaults;
    }
  }, [statusState]);

  const handleMarketplaceNavToggle = async (checked) => {
    const nextModules = {
      ...headerNavModules,
      pricing: {
        ...headerNavModules.pricing,
        enabled: checked,
      },
    };
    setUpdatingMarketplaceNav(true);
    try {
      const res = await API.put('/api/option/', {
        key: 'HeaderNavModules',
        value: JSON.stringify(nextModules),
      });
      const { success, message } = res.data;
      if (!success) {
        showError(message || '模型广场导航开关更新失败');
        return;
      }
      statusDispatch({
        type: 'set',
        payload: {
          ...statusState.status,
          HeaderNavModules: JSON.stringify(nextModules),
        },
      });
      showSuccess(
        checked
          ? '模型广场导航已全局开启'
          : '模型广场导航已全局关闭',
      );
    } catch (error) {
      showError('模型广场导航开关更新失败');
    } finally {
      setUpdatingMarketplaceNav(false);
    }
  };

  const allProps = {
    ...pricingData,
    showRatio,
    setShowRatio,
    viewMode,
    setViewMode,
  };

  return (
    <div className='bg-white'>
      {isAdmin ? (
        <div className='px-4 md:px-6 pt-3'>
          <div className='flex items-center justify-end'>
            <Space spacing={10} align='center'>
              <Text type='secondary'>全局显示模型广场导航</Text>
              <Switch
                checked={Boolean(headerNavModules.pricing?.enabled)}
                disabled={updatingMarketplaceNav}
                onChange={handleMarketplaceNavToggle}
              />
              <Button
                theme='borderless'
                type='tertiary'
                size='small'
                onClick={() => window.open('/console/setting', '_blank')}
              >
                更多设置
              </Button>
            </Space>
          </div>
        </div>
      ) : null}
      <Layout className='pricing-layout'>
        {!isMobile && (
          <Sider className='pricing-scroll-hide pricing-sidebar'>
            <PricingSidebar {...allProps} />
          </Sider>
        )}

        <Content className='pricing-scroll-hide pricing-content'>
          <PricingContent
            {...allProps}
            isMobile={isMobile}
            sidebarProps={allProps}
          />
        </Content>
      </Layout>

      <ImagePreview
        src={pricingData.modalImageUrl}
        visible={pricingData.isModalOpenurl}
        onVisibleChange={(visible) => pricingData.setIsModalOpenurl(visible)}
      />

      <ModelDetailSideSheet
        visible={pricingData.showModelDetail}
        onClose={pricingData.closeModelDetail}
        modelData={pricingData.selectedModel}
        groupRatio={pricingData.groupRatio}
        usableGroup={pricingData.usableGroup}
        currency={pricingData.currency}
        siteDisplayType={pricingData.siteDisplayType}
        tokenUnit={pricingData.tokenUnit}
        displayPrice={pricingData.displayPrice}
        showRatio={allProps.showRatio}
        vendorsMap={pricingData.vendorsMap}
        endpointMap={pricingData.endpointMap}
        autoGroups={pricingData.autoGroups}
        t={pricingData.t}
      />
    </div>
  );
};

export default PricingPage;
