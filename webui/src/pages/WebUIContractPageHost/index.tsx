import { useParams } from 'react-router-dom';
import PageRuntimeHost from './PageRuntimeHost';

export default function WebUIContractPageHost() {
  const { pageId } = useParams<{ pageId: string }>();
  return <PageRuntimeHost key={pageId} pageId={pageId} />;
}
