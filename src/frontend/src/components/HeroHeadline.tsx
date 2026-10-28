import type { CSSProperties } from 'react';
import { Heading } from '@astryxdesign/core/Heading';
import { Text } from '@astryxdesign/core/Text';

const headline = "What's the value of your vehicle?";
const characterInterval = 55;
const typingStart = 200;
const emphasisStyle: CSSProperties & { '--headline-emphasis-delay': string } = {
  '--headline-emphasis-delay': `${typingStart + headline.length * characterInterval + 180}ms`,
};

/** Keep space for the whole heading and let screen readers read it while the letters appear. */
export function HeroHeadline() {
  return (
    <Heading level={1} id="page-title">
      <Text as="span" className="sr-only">
        {headline}
      </Text>
      <Text as="span" className="headline-visual" aria-hidden="true" style={emphasisStyle}>
        {headline.split(' ').map((word, wordIndex) => (
          <Text as="span" key={word} className="headline-phrase">
            {wordIndex > 0 && ' '}
            <Text
              as="span"
              className={`headline-word${word === 'your' ? ' headline-personal' : ''}`}
            >
              {Array.from(word, (letter, index) => (
                <Text
                  as="span"
                  key={index}
                  className="headline-letter"
                  style={{
                    animationDelay: `${typingStart + (headline.indexOf(word) + index) * characterInterval}ms`,
                  }}
                >
                  {letter}
                </Text>
              ))}
              {word === 'your' && (
                <svg
                  className="headline-underline"
                  viewBox="0 0 300 12"
                  preserveAspectRatio="none"
                  aria-hidden="true"
                >
                  <path d="M2 6 L32 4.8 L20 4.3 L88 3.2 C143 2.5 198 3.5 245 2.8 C267 2.2 289 3.2 298 6 C289 9.2 267 10 245 9 C185 8.2 127 9 72 8 L18 7.2 L34 6.7 Z" />
                  <path d="M10 4.6 L58 3.3 L115 3 L45 4.4 Z M21 8 L77 8.4 L46 9 Z" />
                </svg>
              )}
            </Text>
          </Text>
        ))}
      </Text>
    </Heading>
  );
}
