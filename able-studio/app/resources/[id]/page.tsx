import ResourceDetailPanel from "./ResourceDetailPanel";

type PageProps = {
  params: Promise<{ id: string }>;
};

export default async function ResourceDetailPage({ params }: PageProps) {
  const { id } = await params;
  return <ResourceDetailPanel resourceId={id} />;
}
