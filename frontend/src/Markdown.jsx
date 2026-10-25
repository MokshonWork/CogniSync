import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

// Only absolute, fetchable schemes are allowed. Round-tripping through
// react-markdown's urlTransform drops javascript:/data: from links and images.
const SAFE_SCHEME = /^(https?:|mailto:)/i

function safeUrl(url) {
  return SAFE_SCHEME.test(url || '') ? url : ''
}

// Raw HTML is never rendered: react-markdown (without rehype-raw) treats it
// as text, so model output cannot inject markup or scripts.
export default function Markdown({ children, className = '' }) {
  const source = (children || '').toString()
  if (!source.trim()) return null
  return (
    <div className={`markdown-content ${className}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        urlTransform={safeUrl}
        components={{
          a: ({ ...props }) => <a {...props} target="_blank" rel="noopener noreferrer nofollow" />,
          table: ({ ...props }) => (
            <div className="markdown-table-wrap">
              <table {...props} />
            </div>
          ),
        }}
      >
        {source}
      </ReactMarkdown>
    </div>
  )
}
