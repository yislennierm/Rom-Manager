import React from 'react'
import { Alert, Button, Typography } from 'antd'
import type { DatasetCard, DatasetKey, DatasetMeta } from '../types'

type Props = {
  datasets: DatasetCard[]
  meta: Record<DatasetKey, DatasetMeta | null>
  loading: boolean
  error: string | null
  onDownload: (dataset: DatasetCard) => void
}

export const SummaryCards: React.FC<Props> = ({ datasets, meta, loading, error, onDownload }) => (
  <>
    {error && (
      <Alert type="error" showIcon message="Unable to load dataset metadata" description={error} className="app-alert" />
    )}
    <section className="dataset-summary" aria-label="Dataset status">
      {datasets.map((dataset) => {
        const datasetMeta = meta[dataset.key]
        return (
          <article key={dataset.key} className="summary-card">
            <div>
              <Typography.Title level={4} className="summary-title">{dataset.title}</Typography.Title>
              <Typography.Text type="secondary">{dataset.description}</Typography.Text>
            </div>
            <div className="summary-stats">
              <div>
                <Typography.Text className="summary-label">Entries</Typography.Text>
                <Typography.Text className="summary-value">{loading && !datasetMeta ? '...' : datasetMeta?.count ?? 0}</Typography.Text>
              </div>
              <div>
                <Typography.Text className="summary-label">Version</Typography.Text>
                <Typography.Text className="summary-version">
                  {datasetMeta?.version ? datasetMeta.version : 'N/A'}
                </Typography.Text>
              </div>
            </div>
            <Button type="link" onClick={() => onDownload(dataset)} className="summary-link">
              Download JSON
            </Button>
          </article>
        )
      })}
    </section>
  </>
)

export default SummaryCards
