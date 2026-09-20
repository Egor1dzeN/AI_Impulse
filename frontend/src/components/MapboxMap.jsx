import { useEffect, useRef } from 'react';
import mapboxgl from 'mapbox-gl';
import 'mapbox-gl/dist/mapbox-gl.css';
import { MAPBOX_TOKEN } from '../lib/mapbox';
import { resolveTheme } from '../lib/settings';

mapboxgl.accessToken = MAPBOX_TOKEN;

const ROUTE_COLOR = '#FF5A00'; // var(--flare)
const MAP_STYLE = {
  light: 'mapbox://styles/mapbox/streets-v12',
  dark: 'mapbox://styles/mapbox/dark-v11',
};

// One shared interactive-map primitive for both show_map (a single marker)
// and build_route (two markers + a line) — same init/resize/teardown
// lifecycle either way, only what gets drawn on top differs.
export default function MapboxMap({ markers = [], route = null, title }) {
  const containerRef = useRef(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return undefined;

    const map = new mapboxgl.Map({
      container,
      style: MAP_STYLE[resolveTheme()] || MAP_STYLE.light,
      attributionControl: false,
      // Chat feed scrolls the whole page under the mouse — without this,
      // an idle scroll-past over the embedded map would get captured as
      // zoom instead. Requires ctrl/cmd+scroll to zoom, like Google Maps
      // embeds do inside a scrolling page.
      cooperativeGestures: true,
    });

    map.addControl(new mapboxgl.NavigationControl({ showCompass: false }), 'top-right');
    map.addControl(new mapboxgl.AttributionControl({ compact: true }));

    map.on('load', () => {
      if (route && route.length > 1) {
        map.addSource('route', {
          type: 'geojson',
          data: { type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates: route } },
        });
        map.addLayer({
          id: 'route-line',
          type: 'line',
          source: 'route',
          layout: { 'line-join': 'round', 'line-cap': 'round' },
          paint: { 'line-color': ROUTE_COLOR, 'line-width': 4, 'line-opacity': 0.9 },
        });
      }

      markers.forEach(({ lonlat, color }) => {
        new mapboxgl.Marker({ color }).setLngLat(lonlat).addTo(map);
      });

      const bounds = new mapboxgl.LngLatBounds();
      let hasPoint = false;
      markers.forEach(({ lonlat }) => { bounds.extend(lonlat); hasPoint = true; });
      if (route) route.forEach((point) => { bounds.extend(point); hasPoint = true; });
      if (hasPoint) map.fitBounds(bounds, { padding: 40, maxZoom: 15, duration: 0 });
    });

    // The chat message that hosts this map fades/slides into place (see
    // .msg's `rise` animation) and the feed panel itself can still be
    // resizing at mount time, so the container's real size often isn't
    // settled on the very first frame — without this a map can init at
    // zero height and stay blank until the window is manually resized.
    const resizeObserver = new ResizeObserver(() => map.resize());
    resizeObserver.observe(container);

    return () => {
      resizeObserver.disconnect();
      map.remove();
    };
    // markers/route come from one already-completed tool result and never
    // change after this message renders — no need to re-init the map (a
    // full WebGL context rebuild) if the parent re-renders for unrelated
    // reasons.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return <div ref={containerRef} className="mapbox-embed__canvas" role="img" aria-label={title} />;
}
