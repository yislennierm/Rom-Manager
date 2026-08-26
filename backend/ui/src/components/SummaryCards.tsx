import React from 'react'
import { Alert, Button, Card, Flex, Statistic, Typography } from 'antd'
import type { DatasetCard } from '../types'

type Props = {
  datasets: DatasetCard[]
  meta: Record<string, any>
  loading: boolean
  error: string | null
  onDownload: (dataset: DatasetCard) => void
}

export const SummaryCards: React.FC<Props> = ({ datasets, meta, loading, error, onDownload }) => (
  <>
    {error && (
      <Alert type="error" showIcon message="Unable to load dataset metadata" description={error} className="app-alert" />
    )}
    <Flex gap="large" wrap className="dataset-summary">
      {datasets.map((dataset) => {
        const datasetMeta = meta[dataset.key]
        return (
          <Card key={dataset.key} className="summary-card">
            <Typography.Title level={5}>{dataset.title}</Typography.Title>
            <Typography.Text>{dataset.description}</Typography.Text>
            <Flex gap="large" className="summary-stats">
              <Statistic title="Entries" value={datasetMeta?.count ?? 0} loading={loading && !datasetMeta} />
              <Statistic
                title="Version"
                value={datasetMeta?.version ? datasetMeta.version : 'N/A'}
                formatter={(val) => (typeof val === 'string' ? val : 'N/A')}
              />
            </Flex>
            <Button type="link" onClick={() => onDownload(dataset)} className="summary-link">
              Download JSON
            </Button>
          </Card>
        )
      })}
    </Flex>
  </>
)

export default SummaryCards
